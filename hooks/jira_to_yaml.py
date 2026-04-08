#!/usr/bin/env python3
"""
jira_to_yaml.py — Fetch open Jira tasks and inject them into a joshua YAML config.

Usage:
    python3 jira_to_yaml.py \
        --yaml ~/prefect-agents/redegal-mecano-dev.yaml \
        --agent vulcan \
        --jira-url https://redegal.atlassian.net \
        --user jorge.vazquez@redegal.com \

        --jql "project=INTCORP AND parent=INTCORP-1 AND statusCategory!=Done AND assignee=currentUser()" \
        --max 10

Env vars (override CLI args):
    JIRA_URL, JIRA_USER, JIRA_TOKEN, JIRA_JQL
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

import yaml


# ── Jira fetch ────────────────────────────────────────────────────────────────

def fetch_jira_tasks(base_url: str, user: str, token: str, jql: str, max_results: int = 10) -> list[str]:
    """Query Jira and return list of task strings for the agent."""
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    payload = json.dumps({
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "description", "issuetype", "priority", "status", "key"],
    }).encode()

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/api/3/search/jql",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {creds}",
        },
    )

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
    except Exception as e:
        print(f"[jira_to_yaml] ERROR fetching Jira: {e}", file=sys.stderr)
        return []

    tasks = []
    for issue in data.get("issues", []):
        key = issue["key"]
        fields = issue.get("fields", {})
        summary = fields.get("summary", "(sin titulo)")
        itype = fields.get("issuetype", {}).get("name", "Task")
        priority = fields.get("priority", {}).get("name", "")

        line = f"[{key}] {itype}"
        if priority and priority not in ("Medium", "Normal"):
            line += f" ({priority})"
        line += f": {summary}"
        tasks.append(line)

    return tasks


# ── YAML update ───────────────────────────────────────────────────────────────

def update_yaml_tasks(yaml_path: Path, agent_name: str, tasks: list[str]) -> None:
    """Overwrite agent.tasks in the YAML file with tasks from Jira."""
    data = yaml.safe_load(yaml_path.read_text())

    agents = data.get("agents", {})
    if agent_name not in agents:
        print(f"[jira_to_yaml] Agent '{agent_name}' not found in {yaml_path}", file=sys.stderr)
        sys.exit(1)

    if isinstance(agents[agent_name], str):
        agents[agent_name] = {"skill": agent_name}

    agents[agent_name]["tasks"] = tasks
    yaml_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Sync Jira tasks into a joshua YAML agent config")
    p.add_argument("--yaml",      required=True,  help="Path to joshua YAML config")
    p.add_argument("--agent",     required=True,  help="Agent name to update (e.g. vulcan)")
    p.add_argument("--jira-url",  default=os.getenv("JIRA_URL", ""),  help="Jira base URL")
    p.add_argument("--user",      default=os.getenv("JIRA_USER", ""), help="Jira user email")
    p.add_argument("--jql",       default=os.getenv("JIRA_JQL",
        "project=INTCORP AND parent=INTCORP-1 AND statusCategory!=Done AND assignee=currentUser()"
    ), help="JQL to filter tasks")
    p.add_argument("--max",       type=int, default=10, help="Max tasks to fetch")
    p.add_argument("--fallback",  action="store_true",
        help="Keep existing tasks if Jira returns nothing")
    args = p.parse_args()

    token = os.getenv("JIRA_TOKEN", "")
    if not args.jira_url or not args.user or not token:
        print("[jira_to_yaml] Missing Jira credentials. Set JIRA_URL, JIRA_USER, JIRA_TOKEN env vars.",
              file=sys.stderr)
        sys.exit(1)

    yaml_path = Path(args.yaml).expanduser().resolve()
    if not yaml_path.exists():
        print(f"[jira_to_yaml] YAML not found: {yaml_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[jira_to_yaml] Fetching tasks — JQL: {args.jql}")
    tasks = fetch_jira_tasks(args.jira_url, args.user, token, args.jql, args.max)

    if not tasks:
        if args.fallback:
            print("[jira_to_yaml] No tasks found — keeping existing tasks (--fallback)")
            return
        else:
            print("[jira_to_yaml] No open Jira tasks found — agent will have empty task list")

    update_yaml_tasks(yaml_path, args.agent, tasks)

    if tasks:
        print(f"[jira_to_yaml] {len(tasks)} task(s) -> '{args.agent}.tasks' in {yaml_path.name}:")
        for t in tasks:
            print(f"  -> {t}")
    else:
        print(f"[jira_to_yaml] Cleared '{args.agent}.tasks' in {yaml_path.name}")


if __name__ == "__main__":
    main()
