"""Custom command template runner."""

import subprocess
import time
from pathlib import Path

from joshua.runners.base import LLMRunner, RunResult


class CustomRunner(LLMRunner):
    """Run tasks via a user-defined command template.

    The template can use these placeholders:
        {prompt_file}  - Path to a file containing the prompt (recommended)
        {cwd}          - Working directory path
        {timeout}      - Timeout in seconds

    Note: {prompt} is intentionally not supported — large prompts exceed shell
    argument limits (~131KB). Always use {prompt_file} instead.

    Example config:
        runner:
          type: custom
          command: "my-tool --input {prompt_file} --dir {cwd}"
    """

    @property
    def name(self) -> str:
        return "custom"

    def _run_impl(
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
                error_type="error",
            )

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

        # Always write to a named file — never pass prompt as shell arg
        prompt_dir = Path(cwd) / ".joshua" / "temp"
        prompt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        prompt_file = prompt_dir / f"prompt_{int(time.monotonic() * 1000)}.md"
        prompt_file.write_text(full_prompt, encoding="utf-8")
        prompt_file.chmod(0o600)

        try:
            try:
                cmd = command_template.format(
                    prompt_file=str(prompt_file),
                    cwd=cwd,
                    timeout=timeout,
                )
            except KeyError as e:
                return RunResult(
                    success=False, output="", exit_code=-1, duration_seconds=0,
                    error=f"Unknown placeholder in command template: {e}. "
                          f"Valid: {{prompt_file}}, {{cwd}}, {{timeout}}",
                    error_type="error",
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
                error_type="error" if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False, output="", exit_code=-1,
                duration_seconds=float(timeout),
                error=f"Timeout after {timeout}s",
                error_type="timeout",
            )
        finally:
            # Clean up prompt file and old temp files (>1h)
            try:
                prompt_file.unlink(missing_ok=True)
                for old in prompt_dir.glob("prompt_*.md"):
                    if time.time() - old.stat().st_mtime > 3600:
                        old.unlink(missing_ok=True)
            except OSError:
                pass
