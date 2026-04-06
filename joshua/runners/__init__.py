"""LLM Runner implementations."""

from joshua.runners.base import LLMRunner, RunResult
from joshua.runners.claude import ClaudeRunner
from joshua.runners.codex import CodexRunner
from joshua.runners.aider import AiderRunner
from joshua.runners.custom import CustomRunner

RUNNERS = {
    "claude": ClaudeRunner,
    "codex": CodexRunner,
    "aider": AiderRunner,
    "custom": CustomRunner,
}


def runner_factory(config: dict) -> LLMRunner:
    """Create an LLM runner from config."""
    runner_config = config.get("runner", {})
    runner_type = runner_config.get("type", "claude")

    cls = RUNNERS.get(runner_type)
    if cls is None:
        raise ValueError(f"Unknown runner type: {runner_type}. Options: {list(RUNNERS.keys())}")

    return cls(runner_config)
