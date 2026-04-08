#!/usr/bin/env python3
"""
smoke_gate.py — Run smoke tests before deploy (pre_deploy hook).
Exits 1 if tests fail, blocking the deploy.

Usage:
    python3 smoke_gate.py --tests ~/projects/redegal-mecano/tests/smoke_test.py
    python3 smoke_gate.py --url http://localhost:3100/health

Env vars: JOSHUA_CYCLE, JOSHUA_PROJECT, JOSHUA_VERDICT
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request


def check_health(url: str, timeout: int = 10) -> bool:
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(resp.read())
        status = data.get("status", "")
        ok = status in ("ok", "healthy", "up")
        print(f"[smoke_gate] {url} → status={status} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as e:
        print(f"[smoke_gate] Health check failed: {e}", file=sys.stderr)
        return False


def run_pytest(test_path: str, extra_args: list[str]) -> bool:
    cmd = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"] + extra_args
    print(f"[smoke_gate] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    cycle = os.getenv("JOSHUA_CYCLE", "?")
    project = os.getenv("JOSHUA_PROJECT", "project")

    p = argparse.ArgumentParser(description="Smoke test gate — blocks deploy on failure")
    p.add_argument("--tests",   default="", help="Path to pytest smoke test file")
    p.add_argument("--url",     default="", help="Health check URL to verify")
    p.add_argument("--timeout", type=int, default=10)
    args = p.parse_args()

    print(f"[smoke_gate] Pre-deploy smoke test — {project} cycle {cycle}")
    ok = True

    if args.url:
        ok = check_health(args.url, args.timeout) and ok

    if args.tests:
        ok = run_pytest(args.tests, []) and ok

    if not ok:
        print("[smoke_gate] FAILED — blocking deploy", file=sys.stderr)
        sys.exit(1)

    print("[smoke_gate] All checks passed — deploy can proceed")


if __name__ == "__main__":
    main()
