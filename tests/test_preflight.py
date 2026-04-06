"""Tests for pre-flight checks."""

import pytest
from unittest.mock import patch, MagicMock, mock_open

from joshua.utils.preflight import (
    check_disk_space, check_memory, wait_for_memory,
    docker_cleanup, run_preflight,
)


class TestDiskSpace:
    @patch("os.statvfs")
    def test_enough_space(self, mock_statvfs):
        mock_statvfs.return_value = MagicMock(
            f_bavail=10 * 1024**3, f_frsize=1  # 10 GB
        )
        assert check_disk_space(2.0) is True

    @patch("os.statvfs")
    def test_low_space(self, mock_statvfs):
        mock_statvfs.return_value = MagicMock(
            f_bavail=1 * 1024**3, f_frsize=1  # 1 GB
        )
        assert check_disk_space(2.0) is False

    @patch("os.statvfs", side_effect=AttributeError)
    def test_unsupported_platform(self, mock_statvfs):
        assert check_disk_space(2.0) is True


class TestDockerCleanup:
    @patch("subprocess.run")
    def test_cleanup_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert docker_cleanup() is True
        assert mock_run.call_count == 3

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_docker_not_installed(self, mock_run):
        assert docker_cleanup() is False


class TestRunPreflight:
    def test_no_config(self):
        assert run_preflight({}) == []

    def test_empty_preflight(self):
        assert run_preflight({"sprint": {"preflight": {}}}) == []

    @patch("joshua.utils.preflight.check_disk_space", return_value=True)
    def test_disk_ok(self, mock_check):
        config = {"sprint": {"preflight": {"disk_min_gb": 2.0}}}
        assert run_preflight(config) == []

    @patch("joshua.utils.preflight.check_disk_space", return_value=False)
    def test_disk_low_no_cleanup(self, mock_check):
        config = {"sprint": {"preflight": {"disk_min_gb": 5.0}}}
        warnings = run_preflight(config)
        assert len(warnings) == 1
        assert "5.0 GB" in warnings[0]

    @patch("joshua.utils.preflight.docker_cleanup", return_value=True)
    @patch("joshua.utils.preflight.check_disk_space", side_effect=[False, True])
    def test_disk_low_cleanup_fixes(self, mock_check, mock_cleanup):
        config = {"sprint": {"preflight": {"disk_min_gb": 2.0, "docker_cleanup": True}}}
        assert run_preflight(config) == []
        mock_cleanup.assert_called_once()

    @patch("joshua.utils.preflight.docker_cleanup", return_value=True)
    @patch("joshua.utils.preflight.check_disk_space", return_value=False)
    def test_disk_low_cleanup_not_enough(self, mock_check, mock_cleanup):
        config = {"sprint": {"preflight": {"disk_min_gb": 2.0, "docker_cleanup": True}}}
        warnings = run_preflight(config)
        assert len(warnings) == 1
        assert "after cleanup" in warnings[0]


class TestCheckMemory:
    @patch("builtins.open", mock_open(
        read_data="MemTotal:       32000000 kB\nMemAvailable:    8000000 kB\n"))
    def test_enough_memory(self):
        assert check_memory(4.0) is True

    @patch("builtins.open", mock_open(
        read_data="MemTotal:       32000000 kB\nMemAvailable:     500000 kB\n"))
    def test_low_memory(self):
        assert check_memory(2.0) is False

    @patch("builtins.open", side_effect=OSError)
    @patch("subprocess.run", side_effect=OSError)
    def test_unsupported_platform(self, mock_run, mock_file):
        # Can't check — returns True (assume OK)
        assert check_memory(4.0) is True


class TestWaitForMemory:
    @patch("joshua.utils.preflight.check_memory", return_value=True)
    def test_immediate_ok(self, mock_check):
        assert wait_for_memory(4.0) is True
        mock_check.assert_called_once_with(4.0)

    @patch("joshua.utils.preflight.time.sleep")
    @patch("joshua.utils.preflight.check_memory", side_effect=[False, False, True])
    def test_waits_then_ok(self, mock_check, mock_sleep):
        assert wait_for_memory(4.0, timeout=60, poll=5) is True

    @patch("joshua.utils.preflight.time.monotonic", side_effect=[0, 0, 100, 200, 400])
    @patch("joshua.utils.preflight.time.sleep")
    @patch("joshua.utils.preflight.check_memory", return_value=False)
    def test_timeout(self, mock_check, mock_sleep, mock_time):
        assert wait_for_memory(4.0, timeout=300, poll=15) is False


class TestRunPreflightMemory:
    @patch("joshua.utils.preflight.check_memory", return_value=True)
    def test_memory_ok(self, mock_check):
        config = {"preflight": {"min_memory_gb": 4}}
        assert run_preflight(config) == []

    @patch("joshua.utils.preflight.check_memory", return_value=False)
    def test_memory_low(self, mock_check):
        config = {"preflight": {"min_memory_gb": 4}}
        warnings = run_preflight(config)
        assert len(warnings) == 1
        assert "Memory below 4 GB" in warnings[0]

    @patch("joshua.utils.preflight.wait_for_memory", return_value=False)
    def test_memory_wait_timeout(self, mock_wait):
        config = {"preflight": {"min_memory_gb": 4, "memory_wait_timeout": 120}}
        warnings = run_preflight(config)
        assert len(warnings) == 1
        assert "after waiting" in warnings[0]

    @patch("joshua.utils.preflight.wait_for_memory", return_value=True)
    def test_memory_wait_ok(self, mock_wait):
        config = {"preflight": {"min_memory_gb": 4, "memory_wait_timeout": 120}}
        assert run_preflight(config) == []
        mock_wait.assert_called_once_with(4, timeout=120)
