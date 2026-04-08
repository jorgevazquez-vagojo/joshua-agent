#!/usr/bin/env python3
"""
redis_bus.py — Redis message bus for joshua-agent inter-agent communication.

Agents publish findings/status to Redis channels; other agents or hooks
subscribe to react in real time. Enables async swarm coordination.

Channels:
  joshua:{project}:findings  — agent publishes findings (JSON)
  joshua:{project}:verdict   — WOPR publishes verdict
  joshua:{project}:lock      — project lock status
  joshua:{project}:context   — shared context between agents

Usage as library:
    from joshua.integrations.redis_bus import JoshuaBus
    bus = JoshuaBus(project="redegal-mecano")
    bus.publish("findings", {"agent": "vulcan", "severity": "HIGH", "file": "auth.py"})
    bus.subscribe("verdict", callback=handle_verdict)

Usage as CLI hook (publish findings from file):
    python3 redis_bus.py publish --project redegal-mecano --channel findings --file /tmp/findings.json

Env vars: REDIS_URL (default: redis://localhost:6379/1)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Callable


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")


class JoshuaBus:
    """Redis pub/sub bus for joshua agent coordination."""

    def __init__(self, project: str, redis_url: str = REDIS_URL):
        self.project = project
        self.redis_url = redis_url
        self._r = None

    def _connect(self):
        if self._r is None:
            try:
                import redis
                self._r = redis.from_url(self.redis_url, decode_responses=True)
                self._r.ping()
            except ImportError:
                raise RuntimeError("redis package not installed: pip install redis")
            except Exception as e:
                raise RuntimeError(f"Redis connection failed ({self.redis_url}): {e}")
        return self._r

    def _channel(self, name: str) -> str:
        return f"joshua:{self.project}:{name}"

    def publish(self, channel: str, payload: dict) -> int:
        """Publish a message. Returns number of subscribers that received it."""
        r = self._connect()
        msg = json.dumps({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "project": self.project,
            "channel": channel,
            **payload,
        })
        return r.publish(self._channel(channel), msg)

    def set_context(self, key: str, value: str, ttl: int = 3600):
        """Store shared context (e.g. Gmail findings, security scan output)."""
        r = self._connect()
        r.setex(f"joshua:{self.project}:ctx:{key}", ttl, value)

    def get_context(self, key: str) -> str | None:
        """Read shared context written by another agent or hook."""
        try:
            r = self._connect()
            return r.get(f"joshua:{self.project}:ctx:{key}")
        except Exception:
            return None

    def subscribe(self, channel: str, callback: Callable, timeout: float = 30.0):
        """Subscribe and call callback(message_dict) for each message."""
        r = self._connect()
        ps = r.pubsub()
        ps.subscribe(self._channel(channel))
        deadline = time.monotonic() + timeout
        for msg in ps.listen():
            if time.monotonic() > deadline:
                break
            if msg["type"] == "message":
                try:
                    callback(json.loads(msg["data"]))
                except Exception as e:
                    print(f"[redis_bus] callback error: {e}", file=sys.stderr)

    def push_findings(self, agent: str, cycle: int, findings: list[dict]):
        """Convenience: publish agent findings for WOPR or other agents to read."""
        self.publish("findings", {
            "agent": agent,
            "cycle": cycle,
            "count": len(findings),
            "findings": findings[:20],  # cap at 20 to avoid huge messages
        })

    def set_verdict(self, cycle: int, verdict: str, reasoning: str = ""):
        """WOPR publishes its verdict so other systems can react."""
        self.publish("verdict", {
            "cycle": cycle,
            "verdict": verdict,
            "reasoning": reasoning[:500],
        })


def main():
    p = argparse.ArgumentParser(description="Joshua Redis bus CLI")
    sub = p.add_subparsers(dest="cmd")

    pub_p = sub.add_parser("publish", help="Publish a message to a channel")
    pub_p.add_argument("--project",  required=True)
    pub_p.add_argument("--channel",  required=True)
    pub_p.add_argument("--file",     default="", help="JSON file to publish as payload")
    pub_p.add_argument("--message",  default="", help="Inline JSON message")

    ctx_p = sub.add_parser("set-context", help="Store context in Redis")
    ctx_p.add_argument("--project", required=True)
    ctx_p.add_argument("--key",     required=True)
    ctx_p.add_argument("--value",   default="")
    ctx_p.add_argument("--file",    default="")
    ctx_p.add_argument("--ttl",     type=int, default=3600)

    get_p = sub.add_parser("get-context", help="Read context from Redis")
    get_p.add_argument("--project", required=True)
    get_p.add_argument("--key",     required=True)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    bus = JoshuaBus(args.project)

    if args.cmd == "publish":
        payload = {}
        if args.file:
            payload = json.loads(open(args.file).read())
        elif args.message:
            payload = json.loads(args.message)
        n = bus.publish(args.channel, payload)
        print(f"[redis_bus] Published to {args.channel} ({n} subscribers)")

    elif args.cmd == "set-context":
        value = args.value or (open(args.file).read() if args.file else "")
        bus.set_context(args.key, value, args.ttl)
        print(f"[redis_bus] Context set: {args.key} (TTL={args.ttl}s)")

    elif args.cmd == "get-context":
        val = bus.get_context(args.key)
        if val:
            print(val)
        else:
            print(f"[redis_bus] No context found for key: {args.key}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
