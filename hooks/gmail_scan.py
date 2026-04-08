#!/usr/bin/env python3
"""
gmail_scan.py — Gmail scanner hook for joshua-agent (pre_cycle).

Scans Gmail for emails relevant to the project (bug reports, alerts, feedback)
and writes a summary to a context file that agents read at the start of the cycle.

Uses Gmail API via credentials file (same as jorge-copiloto).

Usage (pre_cycle hook):
    python3 gmail_scan.py \
        --credentials ~/.config/gmail/credentials.json \
        --token-file ~/.config/gmail/token.json \
        --query "subject:(bug OR error OR alert OR fallo) newer_than:1d" \
        --output /tmp/gmail-context.txt \
        --max 10

Env vars: JOSHUA_CYCLE, JOSHUA_PROJECT, GMAIL_CREDENTIALS, GMAIL_TOKEN_FILE
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service(credentials_file: str, token_file: str):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("[gmail_scan] google-api-python-client not installed. pip install google-api-python-client google-auth-oauthlib", file=sys.stderr)
        sys.exit(1)

    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(token_file).write_text(creds.to_json())
        else:
            print(f"[gmail_scan] Token expired or missing. Re-authenticate: {token_file}", file=sys.stderr)
            sys.exit(1)

    return build("gmail", "v1", credentials=creds)


def fetch_emails(service, query: str, max_results: int) -> list[dict]:
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        full = service.users().messages().get(
            userId="me", messageId=msg["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()

        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        snippet = full.get("snippet", "")

        emails.append({
            "id":      msg["id"],
            "subject": headers.get("Subject", "(sin asunto)"),
            "from":    headers.get("From", ""),
            "date":    headers.get("Date", ""),
            "snippet": snippet[:300],
        })

    return emails


def write_context(emails: list[dict], output_file: str, project: str, cycle: str):
    if not emails:
        content = f"[gmail_scan] No relevant emails found for {project} (cycle {cycle})\n"
    else:
        lines = [
            f"# Gmail Context — {project} cycle {cycle}",
            f"# Scanned: {datetime.now().isoformat(timespec='seconds')}",
            f"# {len(emails)} email(s) found\n",
        ]
        for i, e in enumerate(emails, 1):
            lines.append(f"## Email {i}: {e['subject']}")
            lines.append(f"From: {e['from']} | Date: {e['date']}")
            lines.append(f"Preview: {e['snippet']}\n")
        content = "\n".join(lines)

    Path(output_file).write_text(content)
    print(f"[gmail_scan] Written {len(emails)} email(s) to {output_file}")


def main():
    cycle   = os.getenv("JOSHUA_CYCLE", "?")
    project = os.getenv("JOSHUA_PROJECT", "project")

    p = argparse.ArgumentParser(description="Scan Gmail for project-relevant emails")
    p.add_argument("--credentials", default=os.getenv("GMAIL_CREDENTIALS",
                   "~/.config/gmail/credentials.json"))
    p.add_argument("--token-file",  default=os.getenv("GMAIL_TOKEN_FILE",
                   "~/.config/gmail/token.json"))
    p.add_argument("--query",  default=f"subject:(bug OR error OR alert OR fallo) newer_than:1d",
                   help="Gmail search query")
    p.add_argument("--output", default="/tmp/gmail-context.txt",
                   help="Output file for agent context")
    p.add_argument("--max",    type=int, default=10)
    args = p.parse_args()

    creds = str(Path(args.credentials).expanduser())
    token = str(Path(args.token_file).expanduser())

    if not Path(creds).exists():
        print(f"[gmail_scan] Credentials not found: {creds}", file=sys.stderr)
        sys.exit(1)

    print(f"[gmail_scan] Scanning Gmail — query: {args.query}")
    service = get_gmail_service(creds, token)
    emails  = fetch_emails(service, args.query, args.max)
    write_context(emails, args.output, project, cycle)

    if emails:
        print(f"[gmail_scan] {len(emails)} relevant email(s) found — context written for agents")
    else:
        print("[gmail_scan] No relevant emails — agents proceed without Gmail context")


if __name__ == "__main__":
    main()
