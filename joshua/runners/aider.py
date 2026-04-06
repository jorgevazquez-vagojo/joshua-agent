"""Aider CLI runner."""

import subprocess
import time

from joshua.runners.base import LLMRunner, RunResult


class AiderRunner(LLMRunner):
    """Run tasks via Aider CLI."""

    @property
    def name(self) -> str:
        return "aider"

    def run(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        timeout = timeout or self.timeout
        binary = self.config.get("binary", "aider")
        model = self.config.get("model")

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

        cmd = [binary, "--message", full_prompt, "--yes-always"]

        if model:
            cmd.extend(["--model", model])

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            duration = time.monotonic() - start
            return RunResult(
                success=result.returncode == 0,
                output=result.stdout.strip(),
                exit_code=result.returncode,
                duration_seconds=round(duration, 1),
                error=result.stderr.strip() if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False, output="", exit_code=-1,
                duration_seconds=float(timeout),
                error=f"Timeout after {timeout}s",
            )
        except FileNotFoundError:
            return RunResult(
                success=False, output="", exit_code=-1, duration_seconds=0,
                error=f"Binary not found: {binary}. Install: pip install aider-chat",
            )
