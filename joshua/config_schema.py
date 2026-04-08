"""Pydantic schema for joshua-agent YAML config validation."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectConfig(BaseModel):
    name: str
    path: str
    site_url: str = ""  # Live URL for researcher agents (e.g., https://primor.eu)

    model_config = {"extra": "allow"}

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("project.path cannot be empty")
        return v


class RunnerConfig(BaseModel):
    type: Literal["claude", "aider", "codex", "custom"] = "claude"
    timeout: int = Field(default=1800, ge=60, le=86400)
    requests_per_minute: int = Field(default=0, ge=0)
    model: str | None = None
    binary: str | None = None
    command: str | None = None  # for custom runner

    @model_validator(mode="after")
    def custom_requires_command(self) -> RunnerConfig:
        if self.type == "custom" and not self.command:
            raise ValueError("runner.command is required when runner.type is 'custom'")
        return self


class AgentConfig(BaseModel):
    skill: str = ""
    role: str = ""  # legacy alias for skill
    instructions: str = ""

    @model_validator(mode="after")
    def skill_or_role(self) -> AgentConfig:
        # Accept 'role' as alias for 'skill' for backwards compat
        if not self.skill and self.role:
            self.skill = self.role
        if not self.skill:
            raise ValueError("agent must have a 'skill' field")
        return self


class SprintConfig(BaseModel):
    max_cycles: int = Field(default=0, ge=0)
    max_hours: float = Field(default=0.0, ge=0.0)
    max_backoff: int = Field(default=900, ge=10, le=3600)
    health_check_max_failures: int = Field(default=3, ge=1)
    dry_run: bool = False
    deploy_command: str = ""
    revert_command: str = ""
    health_check_command: str = ""
    verdict_policy: dict[str, str] = Field(
        default_factory=lambda: {
            "GO": "deploy",
            "CAUTION": "deploy_with_warning",
            "REVERT": "revert",
        }
    )

    @field_validator("deploy_command", "revert_command", "health_check_command", mode="before")
    @classmethod
    def no_shell_injection(cls, v: str) -> str:
        if v and re.search(r"[;&|`]|\$\(", v):
            raise ValueError(
                "Command contains shell metacharacters. Use a script file instead."
            )
        return v

    @model_validator(mode="after")
    def validate_verdict_policy(self) -> SprintConfig:
        valid_verdicts = {"GO", "CAUTION", "REVERT"}
        valid_actions = {"deploy", "deploy_with_warning", "revert", "skip", "stop"}
        invalid_keys = [key for key in self.verdict_policy if key not in valid_verdicts]
        invalid_values = [value for value in self.verdict_policy.values() if value not in valid_actions]
        if invalid_keys:
            raise ValueError(
                "sprint.verdict_policy keys must be one of GO, CAUTION, REVERT"
            )
        if invalid_values:
            raise ValueError(
                "sprint.verdict_policy values must be one of "
                f"{sorted(valid_actions)}"
            )
        return self

    cycle_delay: int = Field(default=0, ge=0)


class SafetyConfig(BaseModel):
    allowed_commands: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    approval_command: str = ""
    approval_required_actions: list[str] = Field(default_factory=list)

    @field_validator("allowed_commands", "allowed_paths", "approval_required_actions", mode="before")
    @classmethod
    def normalize_lists(cls, value):
        if value is None:
            return []
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> SafetyConfig:
        valid_actions = {"deploy", "revert", "recovery_deploy"}
        invalid = [action for action in self.approval_required_actions if action not in valid_actions]
        if invalid:
            raise ValueError(
                "safety.approval_required_actions must only contain "
                f"{sorted(valid_actions)}"
            )
        if self.approval_required_actions and not self.approval_command:
            raise ValueError(
                "safety.approval_command is required when approval_required_actions is set"
            )
        return self


class MemoryConfig(BaseModel):
    enabled: bool = True
    state_dir: str = ""
    lessons_per_cycle: int = Field(default=3, ge=0, le=20)


class NotificationsConfig(BaseModel):
    type: Literal["none", "telegram", "slack", "webhook", "discord"] = "none"
    token: str = ""
    chat_id: str = ""
    webhook_url: str = ""
    url: str = ""


class JoshuaConfig(BaseModel):
    project: ProjectConfig
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    agents: dict[str, AgentConfig]
    sprint: SprintConfig = Field(default_factory=SprintConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @field_validator("agents")
    @classmethod
    def agents_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("at least one agent must be defined")
        return v

    model_config = {"extra": "allow"}  # allow unknown top-level keys for forward compat
