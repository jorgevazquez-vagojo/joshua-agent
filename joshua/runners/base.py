"""Base LLM Runner interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunResult:
    """Result of an LLM runner execution."""

    success: bool
    output: str
    exit_code: int
    duration_seconds: float
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success


class LLMRunner(ABC):
    """Abstract base for all LLM coding tool runners.

    Implementations wrap CLI tools (Claude, Codex, Aider, etc.)
    that can accept a prompt and produce code changes + output.
    """

    def __init__(self, config: dict):
        self.config = config
        self.timeout = config.get("timeout", 1800)

    @abstractmethod
    def run(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        """Execute an LLM coding task.

        Args:
            prompt: The task/instruction for the LLM.
            cwd: Working directory for the LLM to operate in.
            system_prompt: Optional system-level context.
            timeout: Override default timeout (seconds).
            allowed_tools: List of tools the LLM may use (runner-specific).

        Returns:
            RunResult with success status, output text, and metadata.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Runner identifier for logs."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(timeout={self.timeout})"
