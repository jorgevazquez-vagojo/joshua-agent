#!/usr/bin/env python3
"""
jira_transition.py — Transition a Jira issue to a new status and optionally add a comment.

Used by joshua hooks on_go (close task) and on_revert (flag as bug).

Usage:
    # Close task after GO verdict:
    python3 jira_transition.py --issue INTCORP-93 --transition 4 --comment "Resuelto por Vulcan — WOPR GO"

    # Reopen/flag after REVERT:
    python3 jira_transition.py --issue INTCORP-93 --transition 6 --comment "WOPR REVERT: cambios rechazados"

Jira transition IDs (INTCORP project):
    3 = En Curso   4 = Finalizado   6 = Pendiente

Env vars: JIRA_URL, JIRA_USER, JIRA_TOKEN
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request


def jira_request(method: str, path: str, base_url: str, user: str, token: str, body: dict | None = None):
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "Authorization": f"Basic {creds}",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[jira_transition] HTTP {e.code}: {body[:300]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[jira_transition] ERROR: {e}", file=sys.stderr)
        return None


def transition_issue(base_url: str, user: str, token: str, issue: str, transition_id: str) -> bool:
    result = jira_request("POST", f"/rest/api/3/issue/{issue}/transitions",
                          base_url, user, token,
                          {"transition": {"id": str(transition_id)}})
    return result is not None


def add_comment(base_url: str, user: str, token: str, issue: str, text: str) -> bool:
    body = {
        "body": {
            "version": 1, "type": "doc",
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": text}]}],
        }
    }
    result = jira_request("POST", f"/rest/api/3/issue/{issue}/comment",
                          base_url, user, token, body)
    return result is not None


def main():
    p = argparse.ArgumentParser(description="Transition a Jira issue")
    p.add_argument("--issue",      required=True, help="Jira issue key (e.g. INTCORP-93)")
    p.add_argument("--transition", required=True, help="Transition ID (3=En Curso, 4=Finalizado, 6=Pendiente)")
    p.add_argument("--comment",    default="",    help="Optional comment to add")
    p.add_argument("--jira-url",   default=os.getenv("JIRA_URL", "https://redegal.atlassian.net"))
    p.add_argument("--user",       default=os.getenv("JIRA_USER", "jorge.vazquez@redegal.com"))
    args = p.parse_args()

    token = os.getenv("JIRA_TOKEN", "")
    if not token:
        print("[jira_transition] JIRA_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)

    print(f"[jira_transition] {args.issue} -> transition {args.transition}")
    ok = transition_issue(args.jira_url, args.user, token, args.issue, args.transition)
    if not ok:
        print(f"[jira_transition] Transition failed for {args.issue}", file=sys.stderr)
        sys.exit(1)
    print(f"[jira_transition] Transitioned {args.issue} OK")

    if args.comment:
        ok = add_comment(args.jira_url, args.user, token, args.issue, args.comment)
        print(f"[jira_transition] Comment added: {ok}")


if __name__ == "__main__":
    main()
