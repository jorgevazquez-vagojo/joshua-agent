"""Tests for config loading and validation."""

import os
import pytest
from pathlib import Path

from joshua.config import load_config, _interpolate_env, _walk_interpolate


class TestEnvInterpolation:
    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert _interpolate_env("${TEST_VAR}") == "hello"

    def test_var_with_default(self):
        result = _interpolate_env("${NONEXISTENT_VAR:fallback}")
        assert result == "fallback"

    def test_var_missing_no_default(self):
        result = _interpolate_env("${TOTALLY_MISSING}")
        assert result == "${TOTALLY_MISSING}"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "foo")
        monkeypatch.setenv("B", "bar")
        assert _interpolate_env("${A}-${B}") == "foo-bar"

    def test_no_vars(self):
        assert _interpolate_env("plain text") == "plain text"

    def test_walk_interpolate_nested(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "secret123")
        data = {
            "notifications": {
                "token": "${TOKEN}",
                "labels": ["${TOKEN}", "fixed"],
            },
            "count": 42,
        }
        result = _walk_interpolate(data)
        assert result["notifications"]["token"] == "secret123"
        assert result["notifications"]["labels"][0] == "secret123"
        assert result["notifications"]["labels"][1] == "fixed"
        assert result["count"] == 42


class TestLoadConfig:
    def test_load_valid_yaml(self, sample_config_yaml):
        config = load_config(sample_config_yaml)
        assert config["project"]["name"] == "test-project"
        assert config["runner"]["type"] == "claude"
        assert "dev" in config["agents"]
        assert "qa" in config["agents"]

    def test_load_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_missing_project(self, tmp_dir):
        bad = tmp_dir / "bad.yaml"
        bad.write_text("runner:\n  type: claude\nagents:\n  dev:\n    skill: dev\n")
        with pytest.raises(ValueError, match="project"):
            load_config(bad)

    def test_runner_defaults_to_claude(self, tmp_dir):
        p = tmp_dir / "project"
        p.mkdir()
        cfg_path = tmp_dir / "no-runner.yaml"
        cfg_path.write_text(f"project:\n  name: test\n  path: {p}\nagents:\n  dev:\n    skill: dev\n")
        config = load_config(cfg_path)
        assert config["runner"]["type"] == "claude"

    def test_missing_agents(self, tmp_dir):
        p = tmp_dir / "project"
        p.mkdir()
        bad = tmp_dir / "bad.yaml"
        bad.write_text(f"project:\n  name: test\n  path: {p}\nrunner:\n  type: claude\n")
        with pytest.raises(ValueError, match="agents"):
            load_config(bad)

    def test_invalid_runner_type(self, tmp_dir):
        p = tmp_dir / "project"
        p.mkdir()
        bad = tmp_dir / "bad.yaml"
        bad.write_text(
            f"project:\n  name: test\n  path: {p}\n"
            "runner:\n  type: gpt5\n"
            "agents:\n  dev:\n    skill: dev\n"
        )
        with pytest.raises(ValueError, match="runner.type"):
            load_config(bad)

    def test_tilde_expansion(self, tmp_dir):
        config_path = tmp_dir / "tilde.yaml"
        config_path.write_text(
            "project:\n  name: test\n  path: ~/myproject\n"
            "runner:\n  type: claude\n"
            "agents:\n  dev:\n    skill: dev\n"
        )
        config = load_config(config_path)
        assert "~" not in config["project"]["path"]
        assert os.path.expanduser("~") in config["project"]["path"]
