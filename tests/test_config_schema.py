"""Tests for Pydantic config schema validation."""

import pytest
from pydantic import ValidationError

from joshua.config_schema import (
    JoshuaConfig,
    ProjectConfig,
    RunnerConfig,
    AgentConfig,
)


def _minimal_config(**overrides):
    """Return a minimal valid config dict."""
    base = {
        "project": {"name": "test-project", "path": "/tmp/test"},
        "agents": {"dev": {"skill": "dev"}},
    }
    base.update(overrides)
    return base


class TestValidMinimalConfig:
    def test_passes_validation(self):
        config = _minimal_config()
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.project.name == "test-project"
        assert "dev" in parsed.agents

    def test_runner_defaults_to_claude(self):
        config = _minimal_config()
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.runner.type == "claude"

    def test_sprint_defaults(self):
        config = _minimal_config()
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.sprint.max_cycles == 0
        assert parsed.sprint.max_backoff == 900

    def test_memory_defaults(self):
        config = _minimal_config()
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.memory.enabled is True

    def test_extra_top_level_keys_allowed(self):
        config = _minimal_config()
        config["tracker"] = {"dir": "/tmp/tracker"}
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.project.name == "test-project"


class TestProjectValidation:
    def test_missing_project_name_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            JoshuaConfig.model_validate({
                "project": {"path": "/tmp/test"},
                "agents": {"dev": {"skill": "dev"}},
            })

    def test_missing_project_section_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            JoshuaConfig.model_validate({
                "agents": {"dev": {"skill": "dev"}},
            })

    def test_empty_path_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            ProjectConfig(name="test", path="   ")


class TestAgentsValidation:
    def test_empty_agents_dict_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            JoshuaConfig.model_validate({
                "project": {"name": "test", "path": "/tmp/test"},
                "agents": {},
            })

    def test_agent_with_role_alias(self):
        """Legacy 'role' field should be accepted as alias for 'skill'."""
        config = {
            "project": {"name": "test", "path": "/tmp/test"},
            "agents": {"dev": {"role": "developer"}},
        }
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.agents["dev"].skill == "developer"

    def test_agent_without_skill_or_role_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            AgentConfig(skill="", role="")


class TestRunnerValidation:
    def test_custom_runner_without_command_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            JoshuaConfig.model_validate({
                "project": {"name": "test", "path": "/tmp/test"},
                "runner": {"type": "custom"},
                "agents": {"dev": {"skill": "dev"}},
            })

    def test_custom_runner_with_command_passes(self):
        config = _minimal_config()
        config["runner"] = {"type": "custom", "command": "my-llm-cli"}
        parsed = JoshuaConfig.model_validate(config)
        assert parsed.runner.type == "custom"
        assert parsed.runner.command == "my-llm-cli"

    def test_invalid_runner_type_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            JoshuaConfig.model_validate({
                "project": {"name": "test", "path": "/tmp/test"},
                "runner": {"type": "gpt5"},
                "agents": {"dev": {"skill": "dev"}},
            })

    def test_runner_timeout_below_minimum_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            RunnerConfig(type="claude", timeout=30)

    def test_runner_timeout_above_maximum_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            RunnerConfig(type="claude", timeout=99999)

    def test_runner_timeout_valid(self):
        r = RunnerConfig(type="claude", timeout=600)
        assert r.timeout == 600

    def test_valid_runner_types(self):
        for rtype in ("claude", "aider", "codex"):
            r = RunnerConfig(type=rtype)
            assert r.type == rtype
