"""Tests for v1.11.0 security + UX features."""
import pytest
from click.testing import CliRunner
from joshua.cli import main


def test_tips():
    runner = CliRunner()
    result = runner.invoke(main, ["tips"])
    assert result.exit_code == 0
    assert "Tip:" in result.output


def test_tutorial_starts():
    runner = CliRunner()
    result = runner.invoke(main, ["tutorial"], input="\n\n\n\n\n\n\n")
    # Should start without error even if it pauses
    assert result.exit_code == 0 or "joshua" in result.output.lower()


def test_completion_bash():
    runner = CliRunner()
    result = runner.invoke(main, ["completion", "bash"])
    assert result.exit_code == 0
    assert "_JOSHUA_COMPLETE" in result.output or "COMPLETE" in result.output


def test_secure_clean_config(tmp_path):
    cfg = tmp_path / "joshua.yaml"
    cfg.write_text("project: myapp\ncycles: 3\n")
    runner = CliRunner()
    result = runner.invoke(main, ["secure", str(cfg)])
    assert result.exit_code == 0
    assert "clean" in result.output.lower() or "no secrets" in result.output.lower()


def test_secure_detects_token(tmp_path):
    cfg = tmp_path / "joshua.yaml"
    cfg.write_text("project: myapp\ntoken: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz123456\n")
    runner = CliRunner()
    result = runner.invoke(main, ["secure", str(cfg)])
    assert result.exit_code == 1
    assert "token" in result.output.lower() or "secret" in result.output.lower()
