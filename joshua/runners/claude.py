"""Claude Code CLI runner."""

import subprocess
import time

from joshua.runners.base import LLMRunner, RunResult


class ClaudeRunner(LLMRunner):
    """Run tasks via Claude Code CLI (claude command)."""

    @property
    def name(self) -> str:
        return "claude"

    def run(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        timeout = timeout or self.timeout
        binary = self.config.get("binary", "claude")
        max_turns = self.config.get("max_turns", 30)
        model = self.config.get("model")

        # Build the full prompt
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

        cmd = [binary, "-p", full_prompt, "--max-turns", str(max_turns)]

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])
        elif "allowed_tools" in self.config:
            cmd.extend(["--allowedTools", ",".join(self.config["allowed_tools"])])

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
                success=result.returncode == 0 and len(result.stdout.strip()) > 0,
                output=result.stdout.strip(),
                exit_code=result.returncode,
                duration_seconds=round(duration, 1),
                error=result.stderr.strip() if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False,
                output="",
                exit_code=-1,
                duration_seconds=float(timeout),
                error=f"Timeout after {timeout}s",
            )
        except FileNotFoundError:
            return RunResult(
                success=False,
                output="",
                exit_code=-1,
                duration_seconds=0,
                error=f"Binary not found: {binary}. Install Claude Code: npm install -g @anthropic-ai/claude-code",
            )
