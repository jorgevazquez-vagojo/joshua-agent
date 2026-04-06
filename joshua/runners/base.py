"""Base LLM Runner interface."""

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("joshua")

# Max output chars to keep — prevents token limit issues in downstream prompts
MAX_OUTPUT_CHARS = 50_000


@dataclass
class RunResult:
    """Result of an LLM runner execution."""

    success: bool
    output: str
    exit_code: int
    duration_seconds: float
    error: Optional[str] = None
    error_type: Optional[str] = None  # "timeout" | "binary_not_found" | "rate_limit" | "error"
    metadata: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success

    def truncated_output(self, max_chars: int = MAX_OUTPUT_CHARS) -> str:
        """Return output truncated to max_chars with a notice if truncated."""
        if len(self.output) <= max_chars:
            return self.output
        return self.output[:max_chars] + f"\n\n[... output truncated at {max_chars} chars ...]"


class LLMRunner(ABC):
    """Abstract base for all LLM coding tool runners.

    Implementations wrap CLI tools (Claude, Codex, Aider, etc.)
    that can accept a prompt and produce code changes + output.

    Built-in features:
    - Rate limiting: requests_per_minute config throttles LLM calls
    - Output truncation: outputs >50k chars are capped to avoid token limit issues
    """

    def __init__(self, config: dict):
        self.config = config
        self.timeout = config.get("timeout", 1800)
        self._rpm = config.get("requests_per_minute", 0)  # 0 = unlimited
        self._last_request_time: float = 0.0

    def _rate_limit(self):
        """Block until rate limit allows next request."""
        if not self._rpm:
            return
        min_interval = 60.0 / self._rpm
        elapsed = time.monotonic() - self._last_request_time
        wait = min_interval - elapsed
        if wait > 0:
            log.debug(f"Rate limit: waiting {wait:.1f}s (limit: {self._rpm} rpm)")
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    @abstractmethod
    def _run_impl(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        """Runner-specific implementation."""
        ...

    def run(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        """Execute an LLM coding task with rate limiting and output truncation.

        Args:
            prompt: The task/instruction for the LLM.
            cwd: Working directory for the LLM to operate in.
            system_prompt: Optional system-level context.
            timeout: Override default timeout (seconds).
            allowed_tools: List of tools the LLM may use (runner-specific).

        Returns:
            RunResult with success status, output text, and metadata.
        """
        self._rate_limit()
        result = self._run_impl(prompt, cwd, system_prompt, timeout, allowed_tools)

        # Truncate large outputs before returning to avoid downstream token issues
        if len(result.output) > MAX_OUTPUT_CHARS:
            log.warning(
                f"Runner output truncated from {len(result.output)} to {MAX_OUTPUT_CHARS} chars"
            )
            result.output = result.truncated_output()

        return result

    @property
    @abstractmethod
    def name(self) -> str:
        """Runner identifier for logs."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(timeout={self.timeout})"
