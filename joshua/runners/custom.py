"""Custom command template runner."""

import subprocess
import tempfile
import time
from pathlib import Path

from joshua.runners.base import LLMRunner, RunResult


class CustomRunner(LLMRunner):
    """Run tasks via a user-defined command template.

    The template can use these placeholders:
        {prompt}       - The task prompt (shell-escaped)
        {prompt_file}  - Path to a temp file containing the prompt
        {cwd}          - Working directory path
        {timeout}      - Timeout in seconds

    Example config:
        runner:
          type: custom
          command: "my-tool --input {prompt_file} --dir {cwd}"
    """

    @property
    def name(self) -> str:
        return "custom"

    def run(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        timeout = timeout or self.timeout
        command_template = self.config.get("command")
        if not command_template:
            return RunResult(
                success=False, output="", exit_code=-1, duration_seconds=0,
                error="CustomRunner requires 'command' in runner config",
            )

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

        # Write prompt to temp file for {prompt_file} placeholder
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(full_prompt)
            prompt_file = f.name

        try:
            try:
                cmd = command_template.format(
                    prompt=full_prompt.replace('"', '\\"'),
                    prompt_file=prompt_file,
                    cwd=cwd,
                    timeout=timeout,
                )
            except KeyError as e:
                return RunResult(
                    success=False, output="", exit_code=-1, duration_seconds=0,
                    error=f"Unknown placeholder in command template: {e}. "
                          f"Valid: {{prompt}}, {{prompt_file}}, {{cwd}}, {{timeout}}",
                )

            start = time.monotonic()
            result = subprocess.run(
                cmd,
                shell=True,
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
                success=False, output="", exit_code=-1,
                duration_seconds=float(timeout),
                error=f"Timeout after {timeout}s",
            )
        finally:
            Path(prompt_file).unlink(missing_ok=True)
