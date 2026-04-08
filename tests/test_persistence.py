"""Tests for SQLite sprint persistence (SprintDB)."""
import json
import tempfile
from pathlib import Path

import pytest

from joshua.persistence import SprintDB


@pytest.fixture
def db(tmp_path):
    return SprintDB(db_path=tmp_path / "test.db")


def test_insert_and_get(db):
    db.insert_sprint("abc123", "my-project", {"project": {"name": "my-project"}}, "2026-01-01T00:00:00")
    row = db.get_sprint("abc123")
    assert row is not None
    assert row["sprint_id"] == "abc123"
    assert row["project"] == "my-project"
    assert row["status"] == "running"
    assert isinstance(row["stats"], dict)
    assert isinstance(row["config"], dict)


def test_get_nonexistent_returns_none(db):
    assert db.get_sprint("nope") is None


def test_update_cycle(db):
    db.insert_sprint("s1", "proj", {}, "2026-01-01T00:00:00")
    db.update_cycle("s1", cycle=3, stats={"tasks": 10}, verdict="GO", severity="none", source="json")
    row = db.get_sprint("s1")
    assert row["cycle"] == 3
    assert row["stats"] == {"tasks": 10}
    assert row["last_verdict"] == "GO"
    assert row["last_verdict_severity"] == "none"
    assert row["last_verdict_source"] == "json"


def test_complete_sprint_completed(db):
    db.insert_sprint("s2", "proj", {}, "2026-01-01T00:00:00")
    db.complete_sprint("s2", "completed")
    row = db.get_sprint("s2")
    assert row["status"] == "completed"
    assert row["error"] is None


def test_complete_sprint_failed(db):
    db.insert_sprint("s3", "proj", {}, "2026-01-01T00:00:00")
    db.complete_sprint("s3", "failed", error="crashed")
    row = db.get_sprint("s3")
    assert row["status"] == "failed"
    assert row["error"] == "crashed"


def test_complete_sprint_stopped(db):
    db.insert_sprint("s4", "proj", {}, "2026-01-01T00:00:00")
    db.complete_sprint("s4", "stopped")
    row = db.get_sprint("s4")
    assert row["status"] == "stopped"


def test_mark_interrupted_on_startup(db):
    db.insert_sprint("r1", "proj", {}, "2026-01-01T00:00:00")
    db.insert_sprint("r2", "proj", {}, "2026-01-01T00:00:01")
    db.complete_sprint("r2", "completed")
    count = db.mark_interrupted_on_startup()
    assert count == 1
    assert db.get_sprint("r1")["status"] == "interrupted"
    assert db.get_sprint("r2")["status"] == "completed"


def test_mark_interrupted_none_running(db):
    db.insert_sprint("done", "proj", {}, "2026-01-01T00:00:00")
    db.complete_sprint("done", "completed")
    count = db.mark_interrupted_on_startup()
    assert count == 0


def test_list_sprints_ordered(db):
    db.insert_sprint("a", "proj", {}, "2026-01-01T00:00:00")
    db.insert_sprint("b", "proj", {}, "2026-01-02T00:00:00")
    rows = db.list_sprints()
    assert rows[0]["sprint_id"] == "b"  # most recent first
    assert rows[1]["sprint_id"] == "a"


def test_insert_or_ignore_duplicate(db):
    db.insert_sprint("dup", "proj", {}, "2026-01-01T00:00:00")
    db.insert_sprint("dup", "proj", {}, "2026-01-01T00:00:00")  # no error
    assert len(db.list_sprints()) == 1


def test_stats_json_roundtrip(db):
    stats = {"cycles": 5, "tasks_done": 12, "nested": {"a": 1}}
    db.insert_sprint("js1", "proj", {}, "2026-01-01T00:00:00")
    db.update_cycle("js1", cycle=5, stats=stats, verdict="GO", severity="none", source="json")
    row = db.get_sprint("js1")
    assert row["stats"] == stats


# ── New columns: pid, heartbeat, worker_state ─────────────────────

def test_update_pid(db):
    db.insert_sprint("pid1", "proj", {}, "2026-01-01T00:00:00")
    db.update_pid("pid1", 12345)
    row = db.get_sprint("pid1")
    assert row["pid"] == 12345
    assert row["worker_state"] == "running"


def test_update_heartbeat(db):
    db.insert_sprint("hb1", "proj", {}, "2026-01-01T00:00:00")
    db.update_heartbeat("hb1", 12345)
    row = db.get_sprint("hb1")
    assert row["heartbeat_at"] is not None
    assert row["pid"] == 12345


def test_update_worker_state(db):
    db.insert_sprint("ws1", "proj", {}, "2026-01-01T00:00:00")
    db.update_worker_state("ws1", "stopping")
    row = db.get_sprint("ws1")
    assert row["worker_state"] == "stopping"


def test_get_running_sprints(db):
    db.insert_sprint("run1", "proj", {}, "2026-01-01T00:00:00")
    db.insert_sprint("run2", "proj", {}, "2026-01-01T00:00:01")
    db.complete_sprint("run2", "completed")
    running = db.get_running_sprints()
    assert len(running) == 1
    assert running[0]["sprint_id"] == "run1"


def test_running_count(db):
    assert db.running_count() == 0
    db.insert_sprint("rc1", "proj", {}, "2026-01-01T00:00:00")
    assert db.running_count() == 1
    db.complete_sprint("rc1", "completed")
    assert db.running_count() == 0


def test_complete_sprint_sets_worker_state_exited(db):
    db.insert_sprint("ex1", "proj", {}, "2026-01-01T00:00:00")
    db.update_worker_state("ex1", "running")
    db.complete_sprint("ex1", "completed")
    row = db.get_sprint("ex1")
    assert row["worker_state"] == "exited"


def test_wal_mode(db):
    """Verify WAL mode is active."""
    import sqlite3
    conn = sqlite3.connect(str(db.path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"
