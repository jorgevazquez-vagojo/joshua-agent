#!/usr/bin/env python3
"""
wiki_revert.py — Save WOPR's revert reasoning to the agent wiki (on_revert hook).

When WOPR issues a REVERT, sprint.py writes the gate findings to a file and
sets JOSHUA_REVERT_FINDINGS_FILE. This script reads that file and saves a
structured wiki entry so future cycles learn from the rejection.

Usage (called automatically by joshua on_revert hook):
    python3 wiki_revert.py --wiki-dir ~/prefect-agents/wiki --project redegal-mecano

Env vars set by joshua:
    JOSHUA_CYCLE             Current cycle number
    JOSHUA_PROJECT           Project name
    JOSHUA_REVERT_FINDINGS_FILE  Path to file containing WOPR's reasoning
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


def redact_basic(text: str) -> str:
    """Basic redaction for standalone use (no joshua imports needed)."""
    import re
    # Redact common secret patterns
    patterns = [
        (r'(token|password|secret|key|passwd|pwd)\s*[=:]\s*\S+', r'\1=<REDACTED>', re.IGNORECASE),
        (r'Bearer\s+\S+', 'Bearer <REDACTED>', 0),
        (r'Basic\s+[A-Za-z0-9+/=]{20,}', 'Basic <REDACTED>', 0),
    ]
    for pattern, replacement, flags in patterns:
        text = re.sub(pattern, replacement, text, flags=flags)
    return text


def write_wiki_entry(wiki_dir: Path, project: str, cycle: int, findings: str) -> Path:
    """Write a wiki entry for the revert reasoning."""
    entries_dir = wiki_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)

    # Append to a rolling revert log for this project
    slug = f"{project}--wopr-revert-log"
    entry_path = entries_dir / f"{slug}.md"

    timestamp = datetime.now().isoformat(timespec="seconds")
    safe_findings = redact_basic(findings)

    # Extract key info: first line with VERDICT/REASONING if structured
    lines = safe_findings.strip().splitlines()
    summary = next((l for l in lines if "REASONING:" in l or "REVERT" in l.upper()), lines[0] if lines else "No reasoning captured")
    summary = summary.replace("REASONING:", "").strip()[:200]

    entry = f"\n## Cycle {cycle} — {timestamp}\n\n**Reason:** {summary}\n\n<details>\n<summary>Full WOPR output</summary>\n\n```\n{safe_findings[:3000]}\n```\n\n</details>\n"

    # Append to existing file or create
    if entry_path.exists():
        existing = entry_path.read_text()
        # Keep header if present, append new entry
        entry_path.write_text(existing + entry)
    else:
        header = f"---\ntopic: WOPR Revert Log\nproject: {project}\ntags: [revert, wopr, learning]\nupdated: {timestamp}\nsource: wiki_revert_hook\n---\n\n# WOPR Revert Log — {project}\n\nAutomatically generated. Each entry captures why WOPR rejected changes.\nAgents read this to avoid repeating the same mistakes.\n"
        entry_path.write_text(header + entry)

    return entry_path


def main():
    cycle = int(os.getenv("JOSHUA_CYCLE", "0"))
    project = os.getenv("JOSHUA_PROJECT", "project")
    findings_file = os.getenv("JOSHUA_REVERT_FINDINGS_FILE", "")

    p = argparse.ArgumentParser(description="Save WOPR revert reasoning to agent wiki")
    p.add_argument("--wiki-dir",  required=True, help="Path to wiki directory (e.g. ~/prefect-agents/wiki)")
    p.add_argument("--project",   default=project, help="Project name for wiki entry")
    args = p.parse_args()

    wiki_dir = Path(args.wiki_dir).expanduser().resolve()

    # Read WOPR's findings
    findings = ""
    if findings_file and Path(findings_file).exists():
        findings = Path(findings_file).read_text().strip()

    if not findings:
        findings = f"[Cycle {cycle}] WOPR issued REVERT — no detailed findings captured."

    print(f"[wiki_revert] Saving revert reasoning for {args.project} cycle {cycle} to wiki")
    entry_path = write_wiki_entry(wiki_dir, args.project, cycle, findings)
    print(f"[wiki_revert] Written to {entry_path}")
    print(f"[wiki_revert] Agents will read this in the next cycle to avoid repeating the mistake")


if __name__ == "__main__":
    main()
