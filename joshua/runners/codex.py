"""OpenAI Codex CLI runner."""

from __future__ import annotations

from joshua.runners.base import LLMRunner, RunResult


class CodexRunner(LLMRunner):
    """Run tasks via OpenAI Codex CLI."""

    @property
    def name(self) -> str:
        return "codex"

    def _run_impl(
        self,
        prompt: str,
        cwd: str,
        system_prompt: str = "",
        timeout: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> RunResult:
        timeout = timeout or self.timeout
        binary = self.config.get("binary", "codex")
        model = self.config.get("model")

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

        cmd = [binary, "--prompt", full_prompt, "--approval-mode", "full-auto"]

        if model:
            cmd.extend(["--model", model])

        return self._run_command(
            cmd,
            cwd=cwd,
            timeout=timeout,
            success_requires_output=True,
            binary_not_found_message=(
                f"Binary not found: {binary}. Install: npm install -g @openai/codex"
            ),
        )
