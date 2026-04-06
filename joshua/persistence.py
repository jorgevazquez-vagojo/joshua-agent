"""SQLite persistence for sprint state.

Keeps a durable record of every sprint — running, completed, failed,
or interrupted. The in-memory _registry in server.py remains the source
of truth for live thread management; this module is the durable store.

DB path: JOSHUA_DB env var, default .joshua/sprints.db
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
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
    last_verdict_source   TEXT NOT NULL DEFAULT 'none'
)
"""


def default_db_path() -> Path:
    return Path(os.environ.get("JOSHUA_DB", ".joshua/sprints.db"))


class SprintDB:
    """Thread-safe SQLite store for sprint lifecycle state."""

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── internal ──────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), check_same_thread=False)

    def _init_db(self):
        with self._lock:
            with self._conn() as conn:
                conn.execute(_SCHEMA)

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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """UPDATE sprints SET status=?, error=?, updated_at=?
                       WHERE sprint_id=?""",
                    (status, error, now, sprint_id),
                )

    def mark_interrupted_on_startup(self) -> int:
        """Mark any 'running' sprints from a previous session as 'interrupted'.

        Called once at server startup. Returns count of rows updated.
        """
        now = self._now()
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """UPDATE sprints SET status='interrupted', updated_at=?
                       WHERE status='running'""",
                    (now,),
                )
                count = cur.rowcount
        if count:
            log.warning(
                f"Marked {count} sprint(s) as 'interrupted' from previous session"
            )
        return count

    # ── reads ─────────────────────────────────────────────────────

    def get_sprint(self, sprint_id: str) -> Optional[dict]:
        """Return sprint row as dict, or None if not found."""
        with self._lock:
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
        with self._lock:
            with self._conn() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT * FROM sprints ORDER BY started_at DESC"
                )
                rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["stats"] = json.loads(d.get("stats") or "{}")
        d["config"] = json.loads(d.get("config") or "{}")
        return d
