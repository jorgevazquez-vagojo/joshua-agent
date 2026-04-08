"""Wiki Knowledge Base — Karpa Pattern.

Pipeline: Sources -> raw/ -> WIKI (entries/) -> Q&A Agent -> Output
The LLM writes everything. Agents just steer. Every answer compounds.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from joshua.utils.redact import redact_secrets

log = logging.getLogger("joshua")

MAX_WIKI_PROMPT_CHARS = 3000


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    return text[:80]


def _ensure_dirs(wiki_dir: str):
    """Create wiki directory structure."""
    for sub in ["raw", "entries", "qa-cache"]:
        os.makedirs(os.path.join(wiki_dir, sub), exist_ok=True)


def save_raw(
    agent: str,
    cycle: int,
    task: str,
    content: str,
    project: str = "general",
    wiki_dir: str = "",
):
    """Save raw agent output for later wiki synthesis."""
    if not wiki_dir:
        return
    _ensure_dirs(wiki_dir)

    task = redact_secrets(task)
    content = redact_secrets(content)

    slug = _slugify(task)
    filename = f"{project}--{agent}--c{cycle:04d}--{slug}.md"
    path = os.path.join(wiki_dir, "raw", filename)

    with open(path, "w") as f:
        f.write(f"---\nagent: {agent}\ncycle: {cycle}\n")
        f.write(f"project: {project}\ntask: {task}\n")
        f.write(f"timestamp: {datetime.now().isoformat(timespec='seconds')}\n---\n\n")
        f.write(content)


def write_entry(
    topic: str,
    content: str,
    project: str = "general",
    tags: list | None = None,
    source_agent: str = "system",
    wiki_dir: str = "",
):
    """Write or update a curated wiki entry."""
    if not wiki_dir:
        return
    _ensure_dirs(wiki_dir)

    topic = redact_secrets(topic)
    content = redact_secrets(content)

    slug = _slugify(topic)
    filename = f"{project}--{slug}.md"
    path = os.path.join(wiki_dir, "entries", filename)
    tags_str = ", ".join(tags) if tags else ""

    with open(path, "w") as f:
        f.write(f"---\ntopic: {topic}\nproject: {project}\n")
        f.write(f"tags: [{tags_str}]\nupdated: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"source: {source_agent}\n---\n\n")
        f.write(content)


def search_entries(query: str, project: str | None = None, wiki_dir: str = "") -> list[dict]:
    """Search wiki entries by keyword."""
    if not wiki_dir:
        return []
    entries_dir = os.path.join(wiki_dir, "entries")
    if not os.path.isdir(entries_dir):
        return []

    results = []
    query_lower = query.lower()
    for f in os.listdir(entries_dir):
        if not f.endswith(".md"):
            continue
        if project and not f.startswith(f"{project}--"):
            continue
        path = os.path.join(entries_dir, f)
        try:
            content = Path(path).read_text().lower()
            if query_lower in content or query_lower in f:
                results.append({"file": f, "path": path})
        except Exception:
            continue
    return results


def build_wiki_context(project: str, task_keywords: str = "", wiki_dir: str = "") -> str:
    """Build wiki context string for agent prompts.

    Returns relevant wiki entries as context the agent can reference.
    """
    if not wiki_dir:
        return ""

    entries_dir = os.path.join(wiki_dir, "entries")
    if not os.path.isdir(entries_dir):
        return ""

    parts = []

    # Search for task-relevant entries
    if task_keywords:
        for word in task_keywords.split()[:5]:
            if len(word) < 3:
                continue
            matches = search_entries(word, project, wiki_dir)
            for m in matches[:3]:
                try:
                    content = Path(m["path"]).read_text()
                    if "---" in content[1:]:
                        content = content.split("---", 2)[2].strip()
                    parts.append(f"### {m['file']}\n{content}")
                except Exception:
                    pass

    # If nothing found, get recent project entries
    if not parts:
        for f in sorted(os.listdir(entries_dir)):
            if not f.endswith(".md"):
                continue
            if not f.startswith(f"{project}--"):
                continue
            try:
                content = Path(os.path.join(entries_dir, f)).read_text()
                if "---" in content[1:]:
                    content = content.split("---", 2)[2].strip()
                parts.append(f"### {f}\n{content}")
            except Exception:
                pass

    # Deduplicate
    seen = set()
    unique = []
    for p in parts:
        key = p[:100]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    result = "\n\n".join(unique[:5])
    if result:
        result = "\n--- WIKI KNOWLEDGE BASE ---\n" + result
    return result[:MAX_WIKI_PROMPT_CHARS]


def count_raw_pending(wiki_dir: str) -> int:
    """Count raw files not yet synthesized."""
    raw_dir = os.path.join(wiki_dir, "raw")
    if not os.path.isdir(raw_dir):
        return 0
    return len([f for f in os.listdir(raw_dir) if f.endswith(".md")])


def list_entries(project: str | None = None, wiki_dir: str = "") -> list[dict]:
    """List all wiki entries."""
    if not wiki_dir:
        return []
    entries_dir = os.path.join(wiki_dir, "entries")
    if not os.path.isdir(entries_dir):
        return []

    entries = []
    for f in sorted(os.listdir(entries_dir)):
        if not f.endswith(".md"):
            continue
        if project and not f.startswith(f"{project}--"):
            continue
        entries.append({"file": f, "path": os.path.join(entries_dir, f)})
    return entries
