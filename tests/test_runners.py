"""Tests for LLM runners."""

import pytest
from unittest.mock import patch, MagicMock
import subprocess

from joshua.runners.base import LLMRunner, RunResult
from joshua.runners.claude import ClaudeRunner
from joshua.runners.codex import CodexRunner
from joshua.runners.aider import AiderRunner
from joshua.runners.custom import CustomRunner
from joshua.runners import runner_factory


class TestRunResult:
    def test_truthy_on_success(self):
        r = RunResult(success=True, output="ok", exit_code=0, duration_seconds=1.0)
        assert r
        assert bool(r) is True

    def test_falsy_on_failure(self):
        r = RunResult(success=False, output="", exit_code=1, duration_seconds=1.0)
        assert not r
        assert bool(r) is False


class TestClaudeRunner:
    def test_name(self):
        runner = ClaudeRunner({"type": "claude"})
        assert runner.name == "claude"

    @patch("subprocess.Popen")
    def test_successful_run(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = ("Changes applied successfully.\nFixed 3 bugs.", "")
        process.returncode = 0
        process.pid = 123
        process.poll.return_value = 0
        mock_popen.return_value = process
        runner = ClaudeRunner({"type": "claude", "timeout": 60})
        result = runner.run("fix bugs", "/tmp/project")
        assert result.success
        assert "Changes applied" in result.output
        assert result.exit_code == 0

        # Verify claude was called correctly
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd

    @patch("subprocess.Popen")
    def test_failed_run(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = ("", "Authentication failed")
        process.returncode = 1
        process.pid = 123
        process.poll.return_value = 1
        mock_popen.return_value = process
        runner = ClaudeRunner({"type": "claude"})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert result.exit_code == 1
        assert "Authentication" in result.error

    @patch("subprocess.Popen")
    def test_timeout(self, mock_popen):
        process = MagicMock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=10),
            ("", ""),
        ]
        process.returncode = -9
        process.pid = 123
        process.poll.return_value = None
        mock_popen.return_value = process
        runner = ClaudeRunner({"type": "claude", "timeout": 10})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert "Timeout" in result.error

    @patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_binary_not_found(self, mock_popen):
        runner = ClaudeRunner({"type": "claude"})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert "not found" in result.error

    @patch("subprocess.Popen")
    def test_model_and_tools_passed(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = ("ok", "")
        process.returncode = 0
        process.pid = 123
        process.poll.return_value = 0
        mock_popen.return_value = process
        runner = ClaudeRunner({
            "type": "claude",
            "model": "sonnet",
            "allowed_tools": ["Bash", "Read"],
        })
        runner.run("task", "/tmp")
        cmd = mock_popen.call_args[0][0]
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "--allowedTools" in cmd

    def test_cancel_terminates_active_process(self):
        runner = ClaudeRunner({"type": "claude"})
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 123
        runner._active_process = process
        runner.cancel()
        assert runner._cancel_requested is True


class TestCustomRunner:
    @patch("subprocess.Popen")
    def test_command_template(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = ("result", "")
        process.returncode = 0
        process.pid = 123
        process.poll.return_value = 0
        mock_popen.return_value = process
        runner = CustomRunner({
            "type": "custom",
            "command": "echo {prompt_file}",
        })
        result = runner.run("test prompt", "/tmp")
        assert result.success

    def test_missing_command(self):
        runner = CustomRunner({"type": "custom"})
        result = runner.run("test", "/tmp")
        assert not result.success
        assert "requires" in result.error

    def test_unknown_placeholder(self):
        runner = CustomRunner({"type": "custom", "command": "echo {unknown_var}"})
        result = runner.run("test", "/tmp")
        assert not result.success
        assert "placeholder" in result.error.lower()


class TestRunnerFactory:
    def test_claude(self):
        runner = runner_factory({"runner": {"type": "claude"}})
        assert isinstance(runner, ClaudeRunner)

    def test_codex(self):
        runner = runner_factory({"runner": {"type": "codex"}})
        assert isinstance(runner, CodexRunner)

    def test_aider(self):
        runner = runner_factory({"runner": {"type": "aider"}})
        assert isinstance(runner, AiderRunner)

    def test_custom(self):
        runner = runner_factory({"runner": {"type": "custom", "command": "echo hi"}})
        assert isinstance(runner, CustomRunner)

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown runner"):
            runner_factory({"runner": {"type": "gpt99"}})

    def test_default_is_claude(self):
        runner = runner_factory({"runner": {}})
        assert isinstance(runner, ClaudeRunner)
