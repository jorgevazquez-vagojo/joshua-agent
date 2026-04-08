"""Tests for ProcessManager and Supervisor."""
import os
import pytest
from unittest.mock import patch, MagicMock

from joshua.persistence import SprintDB
from joshua.process_manager import ProcessManager
from joshua.supervisor import Supervisor


@pytest.fixture
def db(tmp_path):
    return SprintDB(db_path=tmp_path / "test.db")


@pytest.fixture
def pm(db, tmp_path):
    return ProcessManager(db, tmp_path / "logs", max_concurrent=5)


class TestProcessManager:
    def test_running_count_empty(self, pm):
        assert pm.running_count() == 0

    def test_running_count_after_insert(self, pm, db):
        db.insert_sprint("s1", "proj", {}, "2026-01-01T00:00:00")
        assert pm.running_count() == 1

    def test_is_alive_nonexistent(self, pm):
        assert pm.is_alive("nope") is False

    def test_is_alive_dead_pid(self, pm, db):
        db.insert_sprint("s2", "proj", {}, "2026-01-01T00:00:00")
        db.update_pid("s2", 999999999)  # PID that doesn't exist
        assert pm.is_alive("s2") is False

    def test_stop_nonexistent_returns_false(self, pm):
        assert pm.stop("nope") is False

    def test_reap_empty(self, pm):
        pm.reap()  # no crash

    def test_max_concurrent(self, pm):
        assert pm.max_concurrent == 5


class TestSupervisor:
    def test_recover_on_startup_marks_interrupted(self, db, pm):
        db.insert_sprint("orphan", "proj", {}, "2026-01-01T00:00:00")
        db.update_pid("orphan", 999999999)  # dead PID

        sup = Supervisor(db, pm, auto_restart=False)
        sup.recover_on_startup()

        row = db.get_sprint("orphan")
        assert row["status"] == "interrupted"

    def test_recover_on_startup_keeps_alive(self, db, pm):
        db.insert_sprint("alive", "proj", {}, "2026-01-01T00:00:00")
        db.update_pid("alive", os.getpid())  # current PID is alive

        sup = Supervisor(db, pm, auto_restart=False)
        sup.recover_on_startup()

        row = db.get_sprint("alive")
        assert row["status"] == "running"  # not interrupted

    def test_start_stop(self, db, pm):
        sup = Supervisor(db, pm, check_interval=1)
        sup.start()
        assert sup._thread.is_alive()
        sup.stop()
        assert not sup._thread.is_alive()
