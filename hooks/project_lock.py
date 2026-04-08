#!/usr/bin/env python3
"""
project_lock.py — Project locking for joshua-agent (prevents concurrent sprint runs).

Uses file-based lock with PID + Redis distributed lock if available.
A sprint acquires the lock at startup and releases it on exit.

Usage as CLI hook (pre_run — blocks if locked):
    python3 project_lock.py acquire --project redegal-mecano --lock-dir ~/prefect-agents/locks
    python3 project_lock.py release --project redegal-mecano --lock-dir ~/prefect-agents/locks
    python3 project_lock.py status  --project redegal-mecano --lock-dir ~/prefect-agents/locks

Usage as library:
    from joshua.utils.project_lock import ProjectLock
    with ProjectLock("redegal-mecano", lock_dir="~/prefect-agents/locks"):
        # sprint runs here
        pass

Env vars: JOSHUA_PROJECT, JOSHUA_LOCK_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


class ProjectLock:
    """File-based project lock with optional Redis backend."""

    def __init__(self, project: str, lock_dir: str = "~/.joshua/locks",
                 timeout: int = 0, redis_url: str = ""):
        self.project  = project
        self.lock_dir = Path(lock_dir).expanduser().resolve()
        self.timeout  = timeout   # 0 = fail immediately if locked
        self.lock_file = self.lock_dir / f"{project}.lock"
        self.redis_url = redis_url or os.getenv("REDIS_URL", "")
        self._redis_key = f"joshua:lock:{project}"

    def _lock_dir_ok(self):
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def acquire(self) -> bool:
        """Acquire lock. Returns True if acquired, False if already locked."""
        self._lock_dir_ok()

        # Try Redis first (distributed)
        if self.redis_url:
            try:
                import redis
                r = redis.from_url(self.redis_url, decode_responses=True)
                acquired = r.set(
                    self._redis_key,
                    json.dumps({"pid": os.getpid(), "ts": datetime.now().isoformat()}),
                    nx=True, ex=3600,  # 1h TTL — stale locks auto-expire
                )
                if acquired:
                    self._write_file_lock()
                    return True
                if self.timeout == 0:
                    return False
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    time.sleep(5)
                    acquired = r.set(self._redis_key, json.dumps({"pid": os.getpid()}),
                                     nx=True, ex=3600)
                    if acquired:
                        self._write_file_lock()
                        return True
                return False
            except Exception:
                pass  # Fall through to file lock

        # File lock fallback
        if self.lock_file.exists():
            info = self._read_file_lock()
            pid = info.get("pid", 0)
            # Check if PID is still alive
            if pid and self._pid_alive(pid):
                if self.timeout == 0:
                    return False
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    time.sleep(5)
                    if not self.lock_file.exists():
                        break
                    info = self._read_file_lock()
                    if not self._pid_alive(info.get("pid", 0)):
                        break
                else:
                    return False
            # Stale lock — take over
            print(f"[project_lock] Stale lock detected (PID {pid} not running) — taking over")

        self._write_file_lock()
        return True

    def release(self):
        """Release the lock."""
        if self.redis_url:
            try:
                import redis
                r = redis.from_url(self.redis_url, decode_responses=True)
                r.delete(self._redis_key)
            except Exception:
                pass
        if self.lock_file.exists():
            self.lock_file.unlink()

    def status(self) -> dict:
        """Return lock status dict."""
        info = self._read_file_lock() if self.lock_file.exists() else {}
        if not info:
            return {"locked": False, "project": self.project}
        pid = info.get("pid", 0)
        alive = self._pid_alive(pid)
        return {
            "locked": alive,
            "project": self.project,
            "pid": pid,
            "since": info.get("ts", ""),
            "stale": not alive,
        }

    def _write_file_lock(self):
        self.lock_file.write_text(json.dumps({
            "pid": os.getpid(),
            "project": self.project,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }))

    def _read_file_lock(self) -> dict:
        try:
            return json.loads(self.lock_file.read_text())
        except Exception:
            return {}

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Project '{self.project}' is locked by another sprint")
        return self

    def __exit__(self, *_):
        self.release()


def main():
    p = argparse.ArgumentParser(description="Joshua project lock manager")
    p.add_argument("action",     choices=["acquire", "release", "status"])
    p.add_argument("--project",  default=os.getenv("JOSHUA_PROJECT", ""))
    p.add_argument("--lock-dir", default=os.getenv("JOSHUA_LOCK_DIR", "~/.joshua/locks"))
    p.add_argument("--timeout",  type=int, default=0, help="Seconds to wait if locked (0=fail fast)")
    args = p.parse_args()

    if not args.project:
        print("[project_lock] --project required", file=sys.stderr)
        sys.exit(1)

    lock = ProjectLock(args.project, args.lock_dir, args.timeout)

    if args.action == "acquire":
        ok = lock.acquire()
        if ok:
            print(f"[project_lock] Lock acquired for '{args.project}' (PID {os.getpid()})")
        else:
            st = lock.status()
            print(f"[project_lock] '{args.project}' is locked by PID {st.get('pid')} since {st.get('since')}", file=sys.stderr)
            sys.exit(1)

    elif args.action == "release":
        lock.release()
        print(f"[project_lock] Lock released for '{args.project}'")

    elif args.action == "status":
        st = lock.status()
        if st["locked"]:
            print(f"[project_lock] LOCKED — project={args.project} pid={st['pid']} since={st['since']}")
        else:
            print(f"[project_lock] FREE — project={args.project}")


if __name__ == "__main__":
    main()
