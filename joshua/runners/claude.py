"""Claude Code CLI runner."""

from joshua.runners.base import LLMRunner, RunResult


class ClaudeRunner(LLMRunner):
    """Run tasks via Claude Code CLI (claude command)."""

    @property
    def name(self) -> str:
        return "claude"

    def _run_impl(
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

        return self._run_command(
            cmd,
            cwd=cwd,
            timeout=timeout,
            success_requires_output=True,
            binary_not_found_message=(
                f"Binary not found: {binary}. Install Claude Code: "
                "npm install -g @anthropic-ai/claude-code"
            ),
        )
