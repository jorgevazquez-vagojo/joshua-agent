"""Agent self-learning: extract lessons from run output, build memory prompts."""

import json
import logging
from pathlib import Path

log = logging.getLogger("joshua")

MAX_LESSONS_PER_AGENT = 30
MAX_PROMPT_CHARS = 2500


def extract_lessons(
    agent_name: str,
    task: str,
    output: str,
    success: bool,
    cycle: int,
    state_dir: Path,
    verdict: str | None = None,
):
    """Extract lessons from agent output and save to lessons file."""
    lessons_dir = state_dir / "memory"
    lessons_dir.mkdir(parents=True, exist_ok=True)

    path = lessons_dir / f"{agent_name}.json"
    lessons = []
    if path.exists():
        try:
            lessons = json.loads(path.read_text())
        except Exception:
            lessons = []

    entry = {
        "cycle": cycle,
        "task": task[:200],
        "success": success,
        "verdict": verdict,
        "errors_found": [],
        "patterns_good": [],
    }

    # Extract structured info from output
    for line in output.split("\n"):
        line = line.strip()
        if not line or len(line) < 10 or len(line) > 200:
            continue
        lower = line.lower()
        if any(k in lower for k in ["error", "bug", "fix", "broken", "fail"]):
            entry["errors_found"].append(line)
        if any(k in lower for k in ["pattern", "best practice", "improvement", "optimize"]):
            entry["patterns_good"].append(line)

    # Only save if we found something useful
    if entry["errors_found"] or entry["patterns_good"]:
        lessons.append(entry)
        lessons = lessons[-MAX_LESSONS_PER_AGENT:]
        path.write_text(json.dumps(lessons, indent=2, default=str))
        log.debug(f"[{agent_name}] Saved lesson ({len(lessons)} total)")


def load_evolved_guidelines(agent_name: str, state_dir: Path) -> str:
    """Load evolved guidelines for an agent."""
    path = state_dir / "memory" / "evolved" / f"{agent_name}.md"
    if path.exists():
        return path.read_text()[:MAX_PROMPT_CHARS]
    return ""


def build_memory_prompt(agent_name: str, state_dir: Path) -> str:
    """Build a memory context string from guidelines + recent lessons."""
    guidelines = load_evolved_guidelines(agent_name, state_dir)

    # Recent lessons
    path = state_dir / "memory" / f"{agent_name}.json"
    recent = ""
    if path.exists():
        try:
            lessons = json.loads(path.read_text())
            items = []
            for lesson in lessons[-5:]:
                errors = lesson.get("errors_found", [])[:3]
                if errors:
                    task = lesson.get("task", "unknown")[:80]
                    items.append(f"- {task}: {', '.join(str(e)[:100] for e in errors)}")
            recent = "\n".join(items)
        except Exception:
            pass

    parts = []
    if guidelines:
        parts.append(f"\n--- EVOLVED GUIDELINES ---\n{guidelines}")
    if recent:
        parts.append(f"\n--- RECENT LEARNINGS ---\n{recent}")

    return "\n".join(parts)[:MAX_PROMPT_CHARS]
