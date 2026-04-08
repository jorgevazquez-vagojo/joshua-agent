from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
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
    error_type: Optional[str] = None  # "timeout" | "binary_not_found" | "rate_limit" | "cancelled" | "error"
    metadata: dict = field(default_factory=dict)

    tokens_out: int = 0  # estimated output tokens (len(output) // 4)

    def __bool__(self) -> bool:
        return self.success

    def is_transient(self) -> bool:
        """True for errors worth retrying (timeout, rate limit)."""
        return self.error_type in ("timeout", "rate_limit")

    def is_terminal(self) -> bool:
        """True for errors that mean the runner can't work at all (binary missing, cancelled)."""
        return self.error_type in ("binary_not_found", "cancelled")

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
        self._active_process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()
        self._cancel_requested = False

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

    def cancel(self):
        """Cancel the currently running subprocess, if any."""
        self._cancel_requested = True
        with self._process_lock:
            process = self._active_process
        if process is not None:
            self._terminate_process(process)

    def _terminate_process(self, process: subprocess.Popen[str]):
        """Terminate a running process, including its process group when possible."""
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()

    def _run_command(
        self,
        cmd: list[str],
        cwd: str,
        timeout: int,
        success_requires_output: bool = False,
        binary_not_found_message: str | None = None,
    ) -> RunResult:
        """Run a subprocess with cancellation support."""
        start = time.monotonic()
        if self._cancel_requested:
            return RunResult(
                success=False,
                output="",
                exit_code=-1,
                duration_seconds=0,
                error="Cancelled before start",
                error_type="cancelled",
            )

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                start_new_session=(os.name != "nt"),
            )
        except FileNotFoundError:
            return RunResult(
                success=False,
                output="",
                exit_code=-1,
                duration_seconds=0,
                error=binary_not_found_message or f"Binary not found: {cmd[0]}",
                error_type="binary_not_found",
            )

        with self._process_lock:
            self._active_process = process

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate_process(process)
            stdout, stderr = process.communicate()
            return RunResult(
                success=False,
                output=stdout.strip(),
                exit_code=-1,
                duration_seconds=float(timeout),
                error=f"Timeout after {timeout}s",
                error_type="timeout",
            )
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None

        duration = time.monotonic() - start
        stdout = stdout.strip()
        stderr = stderr.strip()

        if self._cancel_requested:
            return RunResult(
                success=False,
                output=stdout,
                exit_code=-1,
                duration_seconds=round(duration, 1),
                error="Cancelled",
                error_type="cancelled",
            )

        return RunResult(
            success=process.returncode == 0 and (not success_requires_output or len(stdout) > 0),
            output=stdout,
            exit_code=process.returncode,
            duration_seconds=round(duration, 1),
            error=stderr if process.returncode != 0 else None,
            error_type="error" if process.returncode != 0 else None,
        )

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

        # Estimate output tokens as proxy for cost tracking
        result.tokens_out = len(result.output) // 4

        return result

    @property
    @abstractmethod
    def name(self) -> str:
        """Runner identifier for logs."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(timeout={self.timeout})"
