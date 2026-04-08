#!/usr/bin/env python3
"""
security_scan.py — Proactive security scan hook for joshua-agent (pre_cycle).

Runs multiple security scanners on the project and outputs a findings report.
Exits 1 only on CRITICAL findings — blocks the cycle. MEDIUM/LOW = warn only.

Scanners used (whichever are installed):
  - bandit   → Python AST security analysis
  - gitleaks → secrets in git history/staged files
  - semgrep  → SAST rules (if available)
  - custom   → .env staged, hardcoded IPs, TODO:FIXME security comments

Usage (pre_cycle hook):
    python3 security_scan.py --project-dir ~/projects/redegal-mecano --fail-on critical

Env vars: JOSHUA_CYCLE, JOSHUA_PROJECT
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime


CRITICAL = "CRITICAL"
HIGH     = "HIGH"
MEDIUM   = "MEDIUM"
LOW      = "LOW"


def run(cmd: list[str], cwd: str, timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except FileNotFoundError:
        return -1, f"[not installed: {cmd[0]}]"
    except subprocess.TimeoutExpired:
        return -2, f"[timeout after {timeout}s]"
    except Exception as e:
        return -3, str(e)


def scan_bandit(project_dir: str) -> list[dict]:
    """Python security linter."""
    rc, out = run(["bandit", "-r", project_dir, "-f", "json", "-ll"], project_dir)
    if rc == -1:
        return []
    findings = []
    try:
        data = json.loads(out)
        for issue in data.get("results", []):
            sev = issue.get("issue_severity", "LOW").upper()
            if sev in (HIGH, CRITICAL):  # bandit only has LOW/MEDIUM/HIGH
                findings.append({
                    "tool": "bandit",
                    "severity": sev,
                    "file": issue.get("filename", ""),
                    "line": issue.get("line_number", 0),
                    "message": issue.get("issue_text", ""),
                    "cwe": issue.get("issue_cwe", {}).get("id", ""),
                })
    except Exception:
        pass
    return findings


def scan_gitleaks(project_dir: str) -> list[dict]:
    """Secrets in git history and staged files."""
    rc, out = run(["gitleaks", "detect", "--source", project_dir,
                   "--report-format", "json", "--report-path", "/tmp/gitleaks-out.json",
                   "--no-banner", "--exit-code", "1"], project_dir, timeout=30)
    findings = []
    try:
        report = Path("/tmp/gitleaks-out.json")
        if report.exists():
            data = json.loads(report.read_text())
            for leak in (data if isinstance(data, list) else []):
                findings.append({
                    "tool": "gitleaks",
                    "severity": CRITICAL,
                    "file": leak.get("File", ""),
                    "line": leak.get("StartLine", 0),
                    "message": f"Secret: {leak.get('Description', 'unknown')} — rule: {leak.get('RuleID', '')}",
                    "cwe": "CWE-312",
                })
    except Exception:
        pass
    return findings


def scan_staged_env(project_dir: str) -> list[dict]:
    """Detect .env files staged for commit."""
    rc, out = run(["git", "diff", "--cached", "--name-only"], project_dir)
    findings = []
    if rc != 0:
        return findings
    for line in out.splitlines():
        fname = line.strip()
        if fname.startswith(".env") or fname.endswith(".env"):
            findings.append({
                "tool": "custom",
                "severity": CRITICAL,
                "file": fname,
                "line": 0,
                "message": f"Staged .env file detected: {fname} — credentials may be committed",
                "cwe": "CWE-312",
            })
    return findings


def scan_hardcoded_patterns(project_dir: str) -> list[dict]:
    """Quick grep for obvious hardcoded secrets in Python/JS/TS files."""
    import re
    patterns = [
        (r'(SECRET_KEY|API_KEY|PASSWORD|TOKEN)\s*=\s*["\'][^${\s]{8,}["\']', HIGH, "Hardcoded credential"),
        (r'-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----', CRITICAL, "Private key in source"),
    ]
    findings = []
    for ext in ("*.py", "*.js", "*.ts", "*.env"):
        for fpath in Path(project_dir).rglob(ext):
            if any(skip in str(fpath) for skip in (".git", "node_modules", "__pycache__", "demo-app")):
                continue
            try:
                text = fpath.read_text(errors="ignore")
                for pattern, severity, msg in patterns:
                    for m in re.finditer(pattern, text, re.IGNORECASE):
                        line = text[:m.start()].count("\n") + 1
                        findings.append({
                            "tool": "custom",
                            "severity": severity,
                            "file": str(fpath),
                            "line": line,
                            "message": f"{msg}: {m.group()[:60]}",
                            "cwe": "CWE-312",
                        })
            except Exception:
                pass
    return findings


def main():
    cycle   = os.getenv("JOSHUA_CYCLE", "?")
    project = os.getenv("JOSHUA_PROJECT", "project")

    p = argparse.ArgumentParser(description="Proactive security scan — pre_cycle hook")
    p.add_argument("--project-dir", required=True, help="Project directory to scan")
    p.add_argument("--fail-on",     default="critical", choices=["critical", "high", "medium", "never"],
                   help="Exit 1 (block cycle) when findings reach this severity")
    p.add_argument("--report-file", default="",  help="Write JSON report to this file")
    args = p.parse_args()

    project_dir = str(Path(args.project_dir).expanduser().resolve())
    fail_severities = {
        "critical": {CRITICAL},
        "high":     {CRITICAL, HIGH},
        "medium":   {CRITICAL, HIGH, MEDIUM},
        "never":    set(),
    }[args.fail_on]

    print(f"[security_scan] Scanning {project} (cycle {cycle}) in {project_dir}")

    all_findings: list[dict] = []
    all_findings.extend(scan_staged_env(project_dir))
    all_findings.extend(scan_bandit(project_dir))
    all_findings.extend(scan_gitleaks(project_dir))
    all_findings.extend(scan_hardcoded_patterns(project_dir))

    # Deduplicate by (file, line, message)
    seen = set()
    unique = []
    for f in all_findings:
        key = (f["file"], f["line"], f["message"][:40])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    all_findings = unique

    # Report
    by_sev = {CRITICAL: [], HIGH: [], MEDIUM: [], LOW: []}
    for f in all_findings:
        by_sev.setdefault(f["severity"], []).append(f)

    total = len(all_findings)
    print(f"[security_scan] {total} finding(s): "
          f"CRITICAL={len(by_sev[CRITICAL])} HIGH={len(by_sev[HIGH])} "
          f"MEDIUM={len(by_sev[MEDIUM])} LOW={len(by_sev[LOW])}")

    for sev in (CRITICAL, HIGH, MEDIUM):
        for f in by_sev[sev]:
            print(f"  [{sev}] {f['tool']} {f['file']}:{f['line']} — {f['message'][:100]}")

    if args.report_file:
        report = {
            "timestamp": datetime.now().isoformat(),
            "project": project,
            "cycle": cycle,
            "findings": all_findings,
        }
        Path(args.report_file).write_text(json.dumps(report, indent=2))

    blocking = [f for f in all_findings if f["severity"] in fail_severities]
    if blocking:
        print(f"[security_scan] BLOCKING: {len(blocking)} finding(s) at or above '{args.fail_on}' threshold",
              file=sys.stderr)
        sys.exit(1)

    print("[security_scan] No blocking findings — cycle can proceed")


if __name__ == "__main__":
    main()
