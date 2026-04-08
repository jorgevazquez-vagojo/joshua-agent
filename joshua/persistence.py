"""SQLite persistence for sprint state — single source of truth.

Every sprint lifecycle event is recorded here. The server reads
exclusively from this store; worker processes write directly.

DB path: JOSHUA_DB env var, default .joshua/sprints.db
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("joshua")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sprints (
    sprint_id             TEXT PRIMARY KEY,
    project               TEXT NOT NULL,
    config                TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'running',
    cycle                 INTEGER NOT NULL DEFAULT 0,
    stats                 TEXT NOT NULL DEFAULT '{}',
    started_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    error                 TEXT,
    last_verdict          TEXT,
    last_verdict_severity TEXT NOT NULL DEFAULT 'none',
    last_verdict_source   TEXT NOT NULL DEFAULT 'none',
    pid                   INTEGER,
    heartbeat_at          TEXT,
    worker_state          TEXT NOT NULL DEFAULT 'init'
)
"""

# Columns added after initial schema — migrated via ALTER TABLE
_MIGRATIONS = [
    ("pid", "INTEGER"),
    ("heartbeat_at", "TEXT"),
    ("worker_state", "TEXT NOT NULL DEFAULT 'init'"),
]


def default_db_path() -> Path:
    return Path(os.environ.get("JOSHUA_DB", ".joshua/sprints.db"))


class SprintDB:
    """Process-safe SQLite store for sprint lifecycle state.

    Uses WAL mode for concurrent reads/writes across server + workers.
    Each caller should create its own SprintDB instance (own connection).
    """

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init_db()
        # Restrict DB file permissions — contains sprint configs and error messages
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # ── internal ──────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute(_SCHEMA)
        self._ensure_columns()

    def _ensure_columns(self):
        """Add columns from _MIGRATIONS if missing (backward-compatible upgrade)."""
        with self._conn() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(sprints)")}
            for col_name, col_type in _MIGRATIONS:
                if col_name not in existing:
                    conn.execute(f"ALTER TABLE sprints ADD COLUMN {col_name} {col_type}")
                    log.info(f"Migrated: added column sprints.{col_name}")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── writes ────────────────────────────────────────────────────

    def insert_sprint(
        self,
        sprint_id: str,
        project: str,
        config: dict,
        started_at: str,
    ) -> None:
        """Record a new sprint as 'running'."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO sprints
                   (sprint_id, project, config, status, started_at, updated_at)
                   VALUES (?, ?, ?, 'running', ?, ?)""",
                (sprint_id, project, json.dumps(config), started_at, now),
            )

    def update_cycle(
        self,
        sprint_id: str,
        cycle: int,
        stats: dict,
        verdict: str,
        severity: str,
        source: str,
    ) -> None:
        """Update cycle progress after each completed cycle."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE sprints SET
                       cycle=?, stats=?, last_verdict=?,
                       last_verdict_severity=?, last_verdict_source=?,
                       updated_at=?
                   WHERE sprint_id=?""",
                (cycle, json.dumps(stats), verdict, severity, source, now, sprint_id),
            )

    def complete_sprint(
        self,
        sprint_id: str,
        status: str,  # "completed" | "failed" | "stopped"
        error: Optional[str] = None,
    ) -> None:
        """Mark a sprint as finished."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE sprints SET status=?, error=?, worker_state='exited', updated_at=?
                   WHERE sprint_id=?""",
                (status, error, now, sprint_id),
            )

    def update_heartbeat(self, sprint_id: str, pid: int) -> None:
        """Worker heartbeat — called periodically from worker process."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sprints SET heartbeat_at=?, pid=? WHERE sprint_id=?",
                (now, pid, sprint_id),
            )

    def update_pid(self, sprint_id: str, pid: int) -> None:
        """Record worker PID after spawn."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sprints SET pid=?, worker_state='running', updated_at=? WHERE sprint_id=?",
                (pid, now, sprint_id),
            )

    def update_worker_state(self, sprint_id: str, state: str) -> None:
        """Update worker lifecycle state (init/running/stopping/exited)."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sprints SET worker_state=?, updated_at=? WHERE sprint_id=?",
                (state, now, sprint_id),
            )

    def mark_interrupted_on_startup(self) -> int:
        """Mark any 'running' sprints from a previous session as 'interrupted'.

        Only marks sprints whose worker PID is no longer alive.
        Called once at server startup. Returns count of rows updated.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT sprint_id, pid FROM sprints WHERE status='running'"
            ).fetchall()

        count = 0
        now = self._now()
        for row in rows:
            pid = row["pid"]
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except (OSError, ProcessLookupError):
                    pass
            if not alive:
                with self._conn() as conn:
                    conn.execute(
                        """UPDATE sprints SET status='interrupted', worker_state='exited', updated_at=?
                           WHERE sprint_id=?""",
                        (now, row["sprint_id"]),
                    )
                    count += 1

        if count:
            log.warning(f"Marked {count} sprint(s) as 'interrupted' from previous session")
        return count

    # ── reads ─────────────────────────────────────────────────────

    def get_sprint(self, sprint_id: str) -> Optional[dict]:
        """Return sprint row as dict, or None if not found."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM sprints WHERE sprint_id=?", (sprint_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_sprints(self) -> list[dict]:
        """Return all sprint rows ordered by started_at desc."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM sprints ORDER BY started_at DESC"
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_running_sprints(self) -> list[dict]:
        """Return sprints with status='running'."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM sprints WHERE status='running' ORDER BY started_at DESC"
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_stale_sprints(self, max_age_seconds: int = 90) -> list[dict]:
        """Return running sprints whose heartbeat is older than threshold."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM sprints WHERE status='running'"
            )
            rows = cur.fetchall()

        stale = []
        now = datetime.now(timezone.utc)
        for row in rows:
            hb = row["heartbeat_at"]
            if not hb:
                stale.append(self._row_to_dict(row))
                continue
            try:
                hb_time = datetime.fromisoformat(hb)
                if hb_time.tzinfo is None:
                    hb_time = hb_time.replace(tzinfo=timezone.utc)
                age = (now - hb_time).total_seconds()
                if age > max_age_seconds:
                    stale.append(self._row_to_dict(row))
            except (ValueError, TypeError):
                stale.append(self._row_to_dict(row))
        return stale

    def running_count(self) -> int:
        """Count of sprints with status='running'."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM sprints WHERE status='running'"
            )
            return cur.fetchone()[0]

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["stats"] = json.loads(d.get("stats") or "{}")
        d["config"] = json.loads(d.get("config") or "{}")
        return d
