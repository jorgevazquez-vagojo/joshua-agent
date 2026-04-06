"""Agent Evolution + Wiki Lint/Heal.

Three jobs (run daily or on demand):
1. EVOLVE: Read lesson JSONs -> synthesize evolved guidelines via LLM
2. WIKI SYNTHESIS: Read raw/ -> curate into wiki entries/
3. WIKI LINT: Validate wiki for duplicates, contradictions, staleness
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from joshua.memory.wiki import write_entry

log = logging.getLogger("joshua")


def evolve_agent(agent_name: str, state_dir: Path, runner) -> bool:
    """Synthesize evolved guidelines from accumulated lessons.

    Args:
        agent_name: Name of the agent to evolve.
        state_dir: Path to .joshua/ directory.
        runner: An LLMRunner instance for synthesis.

    Returns:
        True if guidelines were generated.
    """
    lessons_path = state_dir / "memory" / f"{agent_name}.json"
    if not lessons_path.exists():
        log.info(f"[evolve] No lessons for {agent_name}")
        return False

    try:
        lessons = json.loads(lessons_path.read_text())
    except Exception:
        return False

    if not lessons:
        return False

    # Build lesson summary
    lines = []
    for m in lessons[-50:]:
        errors = ", ".join(str(e)[:100] for e in m.get("errors_found", [])[:3])
        patterns = ", ".join(str(p)[:100] for p in m.get("patterns_good", [])[:3])
        cycle = m.get("cycle", "?")
        task = m.get("task", "unknown")[:80]
        line = f"- Cycle {cycle}: {task}"
        if errors:
            line += f" | Errors: {errors}"
        if patterns:
            line += f" | Patterns: {patterns}"
        lines.append(line)

    lessons_text = "\n".join(lines)

    prompt = f"""Synthesize clear, actionable guidelines for agent "{agent_name}".

Based on these accumulated lessons from past sprint cycles:

{lessons_text}

Output a markdown document with sections:
1. **Do** - things the agent should always do
2. **Don't** - things the agent should avoid
3. **Patterns** - recurring patterns that work well
4. **Anti-patterns** - recurring mistakes to avoid

Be concise. Each point should be one line. Only include actionable items."""

    result = runner.run(prompt=prompt, cwd=str(state_dir), timeout=120)
    if not result.success or not result.output.strip():
        log.warning(f"[evolve] Failed for {agent_name}: {result.error}")
        return False

    evolved_dir = state_dir / "memory" / "evolved"
    evolved_dir.mkdir(parents=True, exist_ok=True)

    out_path = evolved_dir / f"{agent_name}.md"
    out_path.write_text(
        f"# Evolved Guidelines - {agent_name}\n"
        f"# Generated: {datetime.now().isoformat()}\n\n"
        f"{result.output.strip()}"
    )
    log.info(f"[evolve] Guidelines written for {agent_name}")
    return True


def synthesize_wiki(project: str, state_dir: Path, runner, wiki_dir: str) -> int:
    """Read raw/ findings and synthesize into wiki entries.

    This is the core Karpa pattern: raw agent output gets curated
    by the LLM into structured wiki knowledge.

    Returns number of entries created.
    """
    raw_dir = os.path.join(wiki_dir, "raw")
    entries_dir = os.path.join(wiki_dir, "entries")

    if not os.path.isdir(raw_dir):
        return 0

    raw_files = sorted(f for f in os.listdir(raw_dir) if f.endswith(".md"))
    if not raw_files:
        return 0

    # Read up to 10 raw files
    batch = raw_files[:10]
    raw_contents = []
    for f in batch:
        try:
            raw_contents.append(f"### {f}\n{Path(os.path.join(raw_dir, f)).read_text()}")
        except Exception:
            continue

    if not raw_contents:
        return 0

    # Existing entries for context
    existing = []
    if os.path.isdir(entries_dir):
        for f in sorted(os.listdir(entries_dir)):
            if f.endswith(".md") and f.startswith(f"{project}--"):
                try:
                    existing.append(Path(os.path.join(entries_dir, f)).read_text())
                except Exception:
                    pass

    existing_ctx = "\n\n---\n\n".join(existing[-5:]) if existing else "No existing entries."
    all_raw = "\n\n---\n\n".join(raw_contents)

    prompt = f"""You are the Wiki Curator for project "{project}".

Read raw agent findings and synthesize into structured wiki entries.

## Existing wiki entries (avoid duplicates):
{existing_ctx[:3000]}

## New raw findings:
{all_raw[:6000]}

## Instructions:
1. Extract ACTIONABLE, REUSABLE knowledge
2. Group related findings into topics
3. Skip one-off fixes or trivial findings
4. Be specific: include file paths, function names

Output entries in this EXACT format (one or more):

===ENTRY===
TOPIC: <clear topic name>
TAGS: <comma-separated>
---
<markdown content>
===END===

Max 5 entries per batch."""

    result = runner.run(prompt=prompt, cwd=str(state_dir), timeout=240)
    if not result.success:
        return 0

    entries_written = 0
    for block in result.output.split("===ENTRY===")[1:]:
        if "===END===" not in block:
            continue
        block = block.split("===END===")[0].strip()

        topic = ""
        tags = []
        content_start = 0
        lines = block.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("TOPIC:"):
                topic = line.split(":", 1)[1].strip()
            elif line.startswith("TAGS:"):
                tags = [t.strip() for t in line.split(":", 1)[1].split(",")]
            elif line.strip() == "---":
                content_start = i + 1
                break

        if not topic:
            continue

        content = "\n".join(lines[content_start:]).strip()
        if not content:
            continue

        write_entry(topic, content, project=project, tags=tags,
                    source_agent="wiki-curator", wiki_dir=wiki_dir)
        entries_written += 1

    # Archive processed raw files
    for f in batch:
        try:
            os.remove(os.path.join(raw_dir, f))
        except Exception:
            pass

    log.info(f"[wiki] Synthesized {entries_written} entries from {len(batch)} raw files")
    return entries_written


def lint_wiki(state_dir: Path, runner, wiki_dir: str) -> str:
    """Validate wiki: find duplicates, contradictions, stale info.

    Returns the lint report text.
    """
    entries_dir = os.path.join(wiki_dir, "entries")
    if not os.path.isdir(entries_dir):
        return "No entries to lint."

    entries = []
    for f in sorted(os.listdir(entries_dir)):
        if not f.endswith(".md"):
            continue
        try:
            content = Path(os.path.join(entries_dir, f)).read_text()
            if "---" in content[1:]:
                content = content.split("---", 2)[2].strip()
            entries.append(f"**{f}**: {content[:300]}")
        except Exception:
            continue

    if len(entries) < 3:
        return "Too few entries to lint."

    prompt = f"""Review these {len(entries)} wiki entries and identify issues:

{chr(10).join(entries)[:8000]}

Check for:
1. Duplicates: entries covering the same topic
2. Contradictions: entries with conflicting information
3. Stale: entries that reference things likely no longer true
4. Gaps: important topics NOT covered

Output format:
DUPLICATES: <file1> + <file2> (reason) | none
CONTRADICTIONS: <file1> vs <file2> (what conflicts) | none
STALE: <file> (why stale) | none
GAPS: <topic that should exist> | none

Be concise. Only flag real issues."""

    result = runner.run(prompt=prompt, cwd=str(state_dir), timeout=120)
    report = result.output if result.success else f"Lint failed: {result.error}"

    # Save report
    report_path = os.path.join(wiki_dir, "lint-report.md")
    with open(report_path, "w") as f:
        f.write(f"# Wiki Lint Report - {datetime.now().isoformat()}\n\n")
        f.write(f"Entries analyzed: {len(entries)}\n\n")
        f.write(report)

    return report
