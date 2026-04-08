#!/usr/bin/env python3
"""
e2e_gate.py — Run Playwright E2E after deploy (post_deploy hook).
Exits 1 if tests fail, triggering a revert in joshua.

Usage:
    python3 e2e_gate.py \
        --e2e-dir ~/projects/redegal-mecano/tests/e2e \
        --base-url http://localhost:3100 \
        --pass Redegal2026!

Env vars: JOSHUA_CYCLE, JOSHUA_PROJECT, JOSHUA_VERDICT, E2E_BASE_URL, E2E_PASS
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def run_e2e(e2e_dir: str, base_url: str, password: str, timeout: int) -> bool:
    env = {
        **os.environ,
        "E2E_BASE_URL": base_url,
        "E2E_PASS": password,
    }
    cmd = ["npx", "playwright", "test", "--reporter=list"]
    print(f"[e2e_gate] Running Playwright E2E in {e2e_dir}")
    result = subprocess.run(cmd, cwd=e2e_dir, env=env, timeout=timeout)
    return result.returncode == 0


def main():
    cycle = os.getenv("JOSHUA_CYCLE", "?")
    project = os.getenv("JOSHUA_PROJECT", "project")

    p = argparse.ArgumentParser(description="Playwright E2E gate — reverts on failure")
    p.add_argument("--e2e-dir",  required=True, help="Directory with playwright.config.ts")
    p.add_argument("--base-url", default=os.getenv("E2E_BASE_URL", "http://localhost:3100"))
    p.add_argument("--pass",     dest="password", default=os.getenv("E2E_PASS", ""))
    p.add_argument("--timeout",  type=int, default=600, help="Max seconds for E2E suite")
    args = p.parse_args()

    if not args.password:
        print("[e2e_gate] E2E_PASS not set", file=sys.stderr)
        sys.exit(1)

    print(f"[e2e_gate] Post-deploy E2E gate — {project} cycle {cycle}")
    ok = run_e2e(args.e2e_dir, args.base_url, args.password, args.timeout)

    if not ok:
        print("[e2e_gate] E2E FAILED — signaling revert to joshua", file=sys.stderr)
        sys.exit(1)

    print("[e2e_gate] E2E passed — deploy validated OK")


if __name__ == "__main__":
    main()
