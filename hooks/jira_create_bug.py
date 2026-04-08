#!/usr/bin/env python3
"""
jira_create_bug.py — Create a Jira bug when WOPR issues a REVERT verdict.

Usage:
    python3 jira_create_bug.py \
        --project INTCORP --parent INTCORP-1 \
        --summary "WOPR REVERT — cycle 7: cambios rechazados" \
        --description "Detalles del revert..."

Env vars: JIRA_URL, JIRA_USER, JIRA_TOKEN, JOSHUA_CYCLE, JOSHUA_PROJECT
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request


def create_bug(base_url: str, user: str, token: str,
               project: str, parent: str, summary: str, description: str) -> str | None:
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()

    fields: dict = {
        "project": {"key": project},
        "summary": summary[:255],
        "description": {
            "version": 1, "type": "doc",
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": description[:10000]}]}],
        },
        "issuetype": {"name": "Bug"},
        "priority": {"name": "Alta"},
    }
    if parent:
        fields["parent"] = {"key": parent}

    payload = json.dumps({"fields": fields}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/api/3/issue",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        key = data.get("key", "")
        print(f"[jira_create_bug] Bug created: {key}")
        return key
    except urllib.error.HTTPError as e:
        print(f"[jira_create_bug] HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[jira_create_bug] ERROR: {e}", file=sys.stderr)
        return None


def main():
    cycle = os.getenv("JOSHUA_CYCLE", "?")
    project_name = os.getenv("JOSHUA_PROJECT", "proyecto")

    p = argparse.ArgumentParser(description="Create a Jira bug on WOPR REVERT")
    p.add_argument("--project",     default="INTCORP",   help="Jira project key")
    p.add_argument("--parent",      default="INTCORP-1", help="Parent epic key")
    p.add_argument("--summary",     default=f"WOPR REVERT — {project_name} ciclo {cycle}",
                   help="Bug summary")
    p.add_argument("--description", default=f"WOPR rechazó los cambios del ciclo {cycle} en {project_name}. Revisar el log del sprint para ver el razonamiento.",
                   help="Bug description")
    p.add_argument("--jira-url",    default=os.getenv("JIRA_URL", "https://redegal.atlassian.net"))
    p.add_argument("--user",        default=os.getenv("JIRA_USER", "jorge.vazquez@redegal.com"))
    args = p.parse_args()

    token = os.getenv("JIRA_TOKEN", "")
    if not token:
        print("[jira_create_bug] JIRA_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)

    key = create_bug(args.jira_url, args.user, token,
                     args.project, args.parent, args.summary, args.description)
    if not key:
        sys.exit(1)


if __name__ == "__main__":
    main()
