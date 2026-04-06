"""Pydantic schema for joshua-agent YAML config validation."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectConfig(BaseModel):
    name: str
    path: str

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
    deploy_command: str = ""
    revert_command: str = ""
    health_check_command: str = ""
    cycle_delay: int = Field(default=0, ge=0)


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
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @field_validator("agents")
    @classmethod
    def agents_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("at least one agent must be defined")
        return v

    model_config = {"extra": "allow"}  # allow unknown top-level keys for forward compat
