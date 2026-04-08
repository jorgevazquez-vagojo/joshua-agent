"""Status dashboard generator."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def get_status(state_dir: str | Path) -> dict:
    """Gather sprint status from state directory.

    Returns a dict with all status info for display.
    """
    state_dir = Path(state_dir)

    status = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "checkpoint": {},
        "memory": {},
        "wiki": {"entries": 0, "raw_pending": 0, "qa_cached": 0},
    }

    # Checkpoint
    checkpoint_path = state_dir / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            status["checkpoint"] = json.loads(checkpoint_path.read_text())
        except Exception:
            pass

    # Agent memory
    memory_dir = state_dir / "memory"
    if memory_dir.is_dir():
        for f in memory_dir.glob("*.json"):
            try:
                lessons = json.loads(f.read_text())
                agent_name = f.stem
                evolved = (memory_dir / "evolved" / f"{agent_name}.md").exists()
                status["memory"][agent_name] = {
                    "lessons": len(lessons),
                    "evolved": evolved,
                }
            except Exception:
                pass

    # Wiki stats
    wiki_dir = state_dir / "wiki"
    if wiki_dir.is_dir():
        entries_dir = wiki_dir / "entries"
        raw_dir = wiki_dir / "raw"
        qa_dir = wiki_dir / "qa-cache"

        if entries_dir.is_dir():
            status["wiki"]["entries"] = len(list(entries_dir.glob("*.md")))
        if raw_dir.is_dir():
            status["wiki"]["raw_pending"] = len(list(raw_dir.glob("*.md")))
        if qa_dir.is_dir():
            status["wiki"]["qa_cached"] = len(list(qa_dir.glob("*.md")))

    return status


def format_status(status: dict) -> str:
    """Format status dict as a human-readable string."""
    lines = []
    lines.append("=" * 50)
    lines.append("  JOSHUA STATUS")
    lines.append(f"  {status['timestamp']}")
    lines.append("=" * 50)

    # Checkpoint
    cp = status.get("checkpoint", {})
    if cp:
        lines.append(f"\nCycle: {cp.get('cycle', '?')}")
        lines.append(f"Project: {cp.get('project', '?')}")
        stats = cp.get("stats", {})
        if stats:
            lines.append(
                f"Verdicts: GO={stats.get('go', 0)} "
                f"CAUTION={stats.get('caution', 0)} "
                f"REVERT={stats.get('revert', 0)}"
            )
    else:
        lines.append("\nNo checkpoint found (sprint not started?)")

    # Memory
    mem = status.get("memory", {})
    if mem:
        lines.append("\nAgent Memory:")
        for agent, info in sorted(mem.items()):
            evolved = " + evolved" if info.get("evolved") else ""
            lines.append(f"  {agent}: {info['lessons']} lessons{evolved}")

    # Wiki
    wiki = status.get("wiki", {})
    lines.append(
        f"\nWiki: {wiki.get('entries', 0)} entries, "
        f"{wiki.get('raw_pending', 0)} raw pending, "
        f"{wiki.get('qa_cached', 0)} QA cached"
    )

    lines.append("\n" + "=" * 50)
    return "\n".join(lines)
