"""Sprint supervisor — monitors worker health and detects zombies.

Runs as a background thread in the server process. Periodically checks
heartbeats, reaps dead workers, and optionally restarts interrupted sprints.
"""
from __future__ import annotations

import logging
import os
import threading

from joshua.persistence import SprintDB
from joshua.process_manager import ProcessManager

log = logging.getLogger("joshua")


class Supervisor:
    """Monitors sprint worker processes and maintains DB consistency."""

    def __init__(
        self,
        db: SprintDB,
        process_manager: ProcessManager,
        check_interval: int = 30,
        heartbeat_timeout: int = 90,
        auto_restart: bool = False,
    ):
        self.db = db
        self.pm = process_manager
        self.check_interval = check_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.auto_restart = auto_restart
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """Start supervisor as daemon thread."""
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="supervisor"
        )
        self._thread.start()
        log.info(f"Supervisor started (interval={self.check_interval}s, "
                 f"heartbeat_timeout={self.heartbeat_timeout}s, "
                 f"auto_restart={self.auto_restart})")

    def stop(self):
        """Stop the supervisor loop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def recover_on_startup(self):
        """Check for orphaned sprints from a previous session.

        Called once at server startup before the supervisor loop begins.
        """
        interrupted = self.db.mark_interrupted_on_startup()
        if interrupted:
            log.warning(f"Server restart: marked {interrupted} sprint(s) as 'interrupted'")

        if self.auto_restart:
            self._restart_interrupted()

    def _loop(self):
        """Main supervisor loop."""
        while not self._stop_event.wait(timeout=self.check_interval):
            try:
                self._check_heartbeats()
                self.pm.reap()
            except Exception as e:
                log.error(f"Supervisor error: {e}")

    def _check_heartbeats(self):
        """Detect workers that stopped sending heartbeats."""
        stale = self.db.get_stale_sprints(max_age_seconds=self.heartbeat_timeout)
        for row in stale:
            sid = row["sprint_id"]
            pid = row.get("pid")

            # Check if process is actually dead
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except (OSError, ProcessLookupError):
                    pass

            if not alive:
                log.warning(
                    f"Sprint {sid} worker (pid={pid}) is dead — marking interrupted"
                )
                self.db.complete_sprint(sid, "interrupted",
                                        error="Worker process died (no heartbeat)")
                if self.auto_restart:
                    self._restart_sprint(row)

    def _restart_interrupted(self):
        """Restart interrupted sprints that have valid configs."""
        interrupted = self.db.list_sprints()
        for row in interrupted:
            if row["status"] == "interrupted" and row.get("config"):
                self._restart_sprint(row)

    def _restart_sprint(self, row: dict):
        """Restart a single sprint from its saved config."""
        sid = row["sprint_id"]
        config = row["config"]
        if not config:
            return

        try:
            from datetime import datetime
            new_id = f"{sid}-r"  # mark as restart
            started_at = datetime.now().isoformat()
            self.db.insert_sprint(new_id, row["project"], config, started_at)
            pid = self.pm.spawn(new_id, config)
            log.info(f"Auto-restarted sprint {sid} as {new_id} (pid={pid})")
        except Exception as e:
            log.error(f"Failed to restart sprint {sid}: {e}")
