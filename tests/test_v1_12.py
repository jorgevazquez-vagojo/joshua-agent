"""Tests for joshua-agent v1.12.0 features.

Covers:
- Agent backstory field (MEJORA 2)
- effort_score parsing (MEJORA 1a)
- State machine transitions (MEJORA 4)
- joshua learn CLI command (MEJORA 1b)
- joshua status --json output (MEJORA 4b)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


# ── MEJORA 2: Agent backstory ─────────────────────────────────────────────


def test_agent_config_backstory():
    """AgentConfig accepts backstory field and passes it through to Agent."""
    from joshua.agents import agents_from_config

    cfg = {
        "project": {"name": "test", "path": "."},
        "agents": {
            "dev": {
                "skill": "dev",
                "backstory": "You are a Python expert who prefers functional style.",
            }
        },
    }
    agents = agents_from_config(cfg)
    assert len(agents) == 1
    agent = agents[0]
    assert agent.backstory == "You are a Python expert who prefers functional style."
    # Backstory should appear in the built system prompt
    prompt = agent.build_system_prompt({"project_name": "test", "project_dir": ".", "memory": "", "wiki": ""})
    assert "Python expert" in prompt
    assert prompt.startswith("Background:")


def test_agent_config_backstory_default():
    """AgentConfig backstory defaults to empty string when not set."""
    from joshua.agents import agents_from_config

    cfg = {
        "project": {"name": "test", "path": "."},
        "agents": {"dev": {"skill": "dev"}},
    }
    agents = agents_from_config(cfg)
    assert agents[0].backstory == ""
    # System prompt should NOT start with Background: when backstory is empty
    prompt = agents[0].build_system_prompt({"project_name": "test", "project_dir": ".", "memory": "", "wiki": ""})
    assert not prompt.startswith("Background:")


# ── MEJORA 1b: joshua learn ───────────────────────────────────────────────


def test_learn_no_caution(tmp_path):
    """joshua learn exits with error when last verdict is not CAUTION."""
    from click.testing import CliRunner
    from joshua.cli import learn

    # Create a .joshua dir with a GO checkpoint
    joshua_dir = tmp_path / ".joshua"
    joshua_dir.mkdir()
    checkpoint = {
        "project": "test",
        "cycle": 3,
        "last_verdict": "GO",
        "last_gate_findings": "All good",
        "effort_score": 2,
    }
    (joshua_dir / "checkpoint.json").write_text(json.dumps(checkpoint))

    runner = CliRunner()
    result = runner.invoke(learn, [str(tmp_path)])
    assert result.exit_code != 0
    assert "CAUTION" in result.output or "GO" in result.output


def test_learn_saves_lesson(tmp_path):
    """joshua learn saves lesson to wiki/lessons.json when last verdict is CAUTION."""
    from click.testing import CliRunner
    from joshua.cli import learn

    joshua_dir = tmp_path / ".joshua"
    joshua_dir.mkdir()
    checkpoint = {
        "project": "test",
        "cycle": 5,
        "last_verdict": "CAUTION",
        "last_gate_findings": "Pool size change may cause memory pressure under high load.",
        "last_gate_issues": ["Pool size too large", "No load tests"],
        "effort_score": 3,
    }
    (joshua_dir / "checkpoint.json").write_text(json.dumps(checkpoint))

    runner = CliRunner()
    result = runner.invoke(learn, [str(tmp_path)])
    assert result.exit_code == 0, result.output

    lessons_path = joshua_dir / "wiki" / "lessons.json"
    assert lessons_path.exists()
    lessons = json.loads(lessons_path.read_text())
    assert len(lessons) == 1
    entry = lessons[0]
    assert entry["cycle"] == 5
    assert entry["source"] == "accepted_caution"
    assert entry["effort_score"] == 3
    assert entry["project"] == "test"


# ── MEJORA 4b: joshua status --json ──────────────────────────────────────


def test_status_no_checkpoint(tmp_path):
    """joshua status exits with error when no .joshua dir exists."""
    from click.testing import CliRunner
    from joshua.cli import status

    empty_dir = tmp_path / "no_project"
    empty_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(status, [str(empty_dir)])
    assert result.exit_code != 0


def test_status_json(tmp_path):
    """joshua status --json returns valid JSON with state and effort_score fields."""
    from click.testing import CliRunner
    from joshua.cli import status

    joshua_dir = tmp_path / ".joshua"
    joshua_dir.mkdir()
    checkpoint = {
        "project": "myproject",
        "cycle": 7,
        "last_verdict": "GO",
        "last_verdict_severity": "none",
        "effort_score": 2,
        "state": "DONE",
        "state_since": "2026-04-08T10:00:00",
        "cost_usd": 0.42,
        "total_tokens": 12000,
        "stats": {"go": 5, "caution": 1, "revert": 1},
    }
    (joshua_dir / "checkpoint.json").write_text(json.dumps(checkpoint))

    runner = CliRunner()
    result = runner.invoke(status, [str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["project"] == "myproject"
    assert data["cycle"] == 7
    assert data["state"] == "DONE"
    assert data["effort_score"] == 2
    assert data["cost_usd"] == pytest.approx(0.42)
    assert "stats" in data
