"""Shared test fixtures."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that auto-cleans."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def minimal_config(tmp_dir):
    """Minimal valid sprint config dict."""
    project_dir = tmp_dir / "my-project"
    project_dir.mkdir()
    return {
        "project": {
            "name": "test-project",
            "path": str(project_dir),
            "deploy": "echo deployed",
            "health_url": "",
        },
        "runner": {
            "type": "custom",
            "command": "echo 'VERDICT: GO\nREASONING: all good\nRISK_AREAS: none\nACTION_ITEMS: none'",
            "timeout": 10,
        },
        "agents": {
            "dev": {"skill": "dev", "tasks": ["improve code quality"]},
            "qa": {"skill": "qa"},
        },
        "sprint": {
            "cycle_sleep": 0,
            "max_changes_per_cycle": 3,
            "max_cycles": 1,
        },
        "memory": {"enabled": True},
    }


@pytest.fixture
def sample_config_yaml(tmp_dir):
    """Write a minimal YAML config file and return its path."""
    config_path = tmp_dir / "sprint.yaml"
    project_dir = tmp_dir / "my-project"
    project_dir.mkdir()
    config_path.write_text(f"""
project:
  name: test-project
  path: {project_dir}

runner:
  type: claude
  timeout: 60

agents:
  dev:
    skill: dev
    tasks:
      - "Review code"
  qa:
    skill: qa

sprint:
  cycle_sleep: 1
  max_cycles: 1

memory:
  enabled: true
""")
    return config_path
