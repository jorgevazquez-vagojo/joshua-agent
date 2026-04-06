"""Aider CLI runner."""

from joshua.runners.base import LLMRunner, RunResult


class AiderRunner(LLMRunner):
    """Run tasks via Aider CLI."""

    @property
    def name(self) -> str:
        return "aider"

    def _run_impl(
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

        return self._run_command(
            cmd,
            cwd=cwd,
            timeout=timeout,
            success_requires_output=False,
            binary_not_found_message=(
                f"Binary not found: {binary}. Install: pip install aider-chat"
            ),
        )
