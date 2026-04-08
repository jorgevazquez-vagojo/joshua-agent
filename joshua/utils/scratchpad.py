"""Shared cycle scratchpad — agents write context for subsequent agents in the same cycle."""
from __future__ import annotations
import json
import time
from pathlib import Path

SCRATCHPAD_FILE = "cycle_context.json"


def read_scratchpad(project_dir: str | Path) -> dict:
    """Read the current cycle scratchpad. Returns empty dict if not found."""
    path = Path(project_dir) / SCRATCHPAD_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def write_scratchpad(project_dir: str | Path, agent_name: str, data: dict) -> None:
    """Write agent's contribution to the scratchpad (merges with existing)."""
    path = Path(project_dir) / SCRATCHPAD_FILE
    scratchpad = read_scratchpad(project_dir)
    scratchpad[agent_name] = {
        **data,
        "_written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(scratchpad, indent=2))


def clear_scratchpad(project_dir: str | Path) -> None:
    """Clear the scratchpad at the start of each cycle."""
    path = Path(project_dir) / SCRATCHPAD_FILE
    if path.exists():
        path.unlink()


def scratchpad_summary(project_dir: str | Path) -> str:
    """Return a human-readable summary for injection into agent prompts."""
    data = read_scratchpad(project_dir)
    if not data:
        return ""
    lines = ["=== Context from previous agents this cycle ==="]
    for agent_name, info in data.items():
        if agent_name.startswith("_"):
            continue
        lines.append(f"\n[{agent_name}]")
        for k, v in info.items():
            if not k.startswith("_"):
                lines.append(f"  {k}: {v}")
    return "\n".join(lines)
