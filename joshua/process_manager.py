"""Process manager — spawns and controls sprint worker processes.

Each sprint runs in its own multiprocessing.Process for full isolation.
State is tracked in SQLite; the process table here is ephemeral and
used only for signal delivery within the current server session.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from joshua.persistence import SprintDB

log = logging.getLogger("joshua")

# Ensure spawn method on all platforms (no fork — no shared memory)
_start_method_set = False


def _ensure_spawn():
    global _start_method_set
    if not _start_method_set:
        try:
            multiprocessing.set_start_method("spawn", force=True)
        except RuntimeError:
            pass  # already set
        _start_method_set = True


class ProcessManager:
    """Manages sprint worker processes."""

    def __init__(self, db: SprintDB, log_dir: Path, max_concurrent: int = 10):
        self.db = db
        self.log_dir = log_dir
        self.max_concurrent = max_concurrent
        self._processes: dict[str, multiprocessing.Process] = {}
        _ensure_spawn()

    def spawn(
        self,
        sprint_id: str,
        config: dict,
        callback_url: Optional[str] = None,
    ) -> int:
        """Spawn a worker process. Returns PID."""
        from joshua.worker import run_worker

        proc = multiprocessing.Process(
            target=run_worker,
            args=(sprint_id, config, str(self.db.path), str(self.log_dir), callback_url),
            name=f"sprint-{sprint_id}",
        )
        proc.start()
        self._processes[sprint_id] = proc
        log.info(f"Spawned worker process {proc.pid} for sprint {sprint_id}")
        return proc.pid

    def stop(self, sprint_id: str) -> bool:
        """Send SIGTERM to worker. Returns True if signal sent."""
        # Try local process table first
        proc = self._processes.get(sprint_id)
        if proc and proc.is_alive():
            proc.terminate()  # sends SIGTERM
            self.db.update_worker_state(sprint_id, "stopping")
            log.info(f"Sent SIGTERM to sprint {sprint_id} (pid={proc.pid})")
            return True

        # Fall back to PID from DB (server may have restarted)
        row = self.db.get_sprint(sprint_id)
        if row and row.get("pid"):
            pid = row["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
                self.db.update_worker_state(sprint_id, "stopping")
                log.info(f"Sent SIGTERM to sprint {sprint_id} (pid={pid}, from DB)")
                return True
            except (OSError, ProcessLookupError):
                log.warning(f"PID {pid} for sprint {sprint_id} is not alive")
                return False

        return False

    def is_alive(self, sprint_id: str) -> bool:
        """Check if worker process is alive."""
        proc = self._processes.get(sprint_id)
        if proc:
            return proc.is_alive()

        # Fall back to PID from DB
        row = self.db.get_sprint(sprint_id)
        if row and row.get("pid"):
            try:
                os.kill(row["pid"], 0)
                return True
            except (OSError, ProcessLookupError):
                return False
        return False

    def running_count(self) -> int:
        """Count of currently running sprints from DB."""
        return self.db.running_count()

    def reap(self):
        """Clean up terminated processes from local table."""
        dead = []
        for sid, proc in self._processes.items():
            if not proc.is_alive():
                proc.join(timeout=1)
                dead.append(sid)
        for sid in dead:
            del self._processes[sid]

    def join_all(self, timeout: int = 30):
        """Wait for all known processes to exit."""
        for sid, proc in self._processes.items():
            if proc.is_alive():
                proc.join(timeout=timeout)
                if proc.is_alive():
                    log.warning(f"Worker {sid} (pid={proc.pid}) did not exit in {timeout}s")

    def stop_all(self):
        """Stop all running sprints."""
        running = self.db.get_running_sprints()
        for row in running:
            self.stop(row["sprint_id"])
