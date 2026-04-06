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

    @patch("subprocess.run")
    def test_successful_run(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Changes applied successfully.\nFixed 3 bugs.",
            stderr="",
        )
        runner = ClaudeRunner({"type": "claude", "timeout": 60})
        result = runner.run("fix bugs", "/tmp/project")
        assert result.success
        assert "Changes applied" in result.output
        assert result.exit_code == 0

        # Verify claude was called correctly
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd

    @patch("subprocess.run")
    def test_failed_run(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Authentication failed",
        )
        runner = ClaudeRunner({"type": "claude"})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert result.exit_code == 1
        assert "Authentication" in result.error

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=10))
    def test_timeout(self, mock_run):
        runner = ClaudeRunner({"type": "claude", "timeout": 10})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert "Timeout" in result.error

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_binary_not_found(self, mock_run):
        runner = ClaudeRunner({"type": "claude"})
        result = runner.run("fix bugs", "/tmp")
        assert not result.success
        assert "not found" in result.error

    @patch("subprocess.run")
    def test_model_and_tools_passed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        runner = ClaudeRunner({
            "type": "claude",
            "model": "sonnet",
            "allowed_tools": ["Bash", "Read"],
        })
        runner.run("task", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "--allowedTools" in cmd


class TestCustomRunner:
    @patch("subprocess.run")
    def test_command_template(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="result", stderr="")
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
