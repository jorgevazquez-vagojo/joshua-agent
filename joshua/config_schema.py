"""Pydantic schema for joshua-agent YAML config validation."""
from __future__ import annotations
import re
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectConfig(BaseModel):
    name: str
    path: str
    deploy: str = ""              # Shell command to deploy on GO verdict
    health_url: str = ""          # HTTP endpoint for health checks
    objective_metric: str = ""    # Shell command that prints a number (lower = better)
    protected_files: list[str] = Field(default_factory=list)  # Globs agents must not modify
    site_url: str = ""             # Live URL for researcher agents (e.g., https://primor.eu)

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("project.path cannot be empty")
        return v

    @field_validator("deploy", "objective_metric", mode="before")
    @classmethod
    def no_shell_injection_deploy(cls, v: str) -> str:
        if v and re.search(r"[;&|`\n\r]|\$[\({a-zA-Z]", v):
            raise ValueError(
                "Command contains shell metacharacters ($VAR, pipes, semicolons, "
                "backticks, newlines). Use a wrapper script instead."
            )
        return v


class RunnerConfig(BaseModel):
    type: Literal["claude", "aider", "codex", "custom"] = "claude"
    timeout: int = Field(default=1800, ge=60, le=86400)
    requests_per_minute: int = Field(default=0, ge=0)
    max_tokens_per_cycle: int = Field(default=0, ge=0)  # 0 = no limit; estimated output tokens
    model: Optional[str] = None
    binary: Optional[str] = None
    command: Optional[str] = None  # for custom runner

    @model_validator(mode="after")
    def custom_requires_command(self) -> RunnerConfig:
        if self.type == "custom" and not self.command:
            raise ValueError("runner.command is required when runner.type is 'custom'")
        return self


class AgentConfig(BaseModel):
    skill: str = ""
    role: str = ""  # legacy alias for skill
    instructions: str = ""
    task_source: Optional[str] = None  # "jira" | None — dynamic task fetching
    task_source_config: dict = Field(default_factory=dict)

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
    cycle_sleep: int = Field(default=300, ge=0)
    revert_sleep: int = Field(default=0, ge=0)  # 0 = use cycle_sleep
    health_check: bool = False
    recovery_deploy: str = ""
    gate_blocking: bool = False
    cross_agent_context: bool = False
    git_strategy: Literal["none", "snapshot", "hillclimb"] = "none"
    trigger: Literal["continuous", "event", "on_demand"] = "continuous"
    poll_interval: int = Field(default=300, ge=30)  # seconds between polls in event mode
    parallel_agents: bool = False  # run work agents concurrently (gate remains sequential)

    @field_validator("recovery_deploy", mode="before")
    @classmethod
    def no_shell_injection(cls, v: str) -> str:
        if v and re.search(r"[;&|`\n\r]|\$[\({a-zA-Z]", v):
            raise ValueError(
                "Command contains shell metacharacters. Use a wrapper script instead."
            )
        return v


class MemoryConfig(BaseModel):
    enabled: bool = True
    state_dir: str = ""
    lessons_per_cycle: int = Field(default=3, ge=0, le=20)
    max_lesson_age_cycles: int = Field(default=50, ge=5)


class NotificationsConfig(BaseModel):
    type: Literal["none", "telegram", "slack", "webhook", "discord"] = "none"
    token: str = ""
    chat_id: str = ""
    webhook_url: str = ""
    url: str = ""


class PreflightConfig(BaseModel):
    min_disk_gb: int = Field(default=0, ge=0)
    min_memory_gb: int = Field(default=0, ge=0)
    memory_wait_timeout: int = Field(default=120, ge=0)
    docker_cleanup: bool = False


class TrackerConfig(BaseModel):
    type: Literal["none", "jira", "github", "filesystem", "linear"] = "none"
    model_config = {"extra": "allow"}  # tracker-specific fields (base_url, project_key, etc.)


class JoshuaConfig(BaseModel):
    project: ProjectConfig
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    agents: dict[str, AgentConfig]
    sprint: SprintConfig = Field(default_factory=SprintConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    preflight: PreflightConfig = Field(default_factory=PreflightConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)

    @field_validator("agents")
    @classmethod
    def agents_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("at least one agent must be defined")
        return v

    model_config = {"extra": "ignore"}  # silently drop unknown top-level keys
