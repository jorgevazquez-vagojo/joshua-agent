"""Verify that declared tools are available before launching agents."""
import shutil
from typing import NamedTuple


class ToolCheckResult(NamedTuple):
    ok: bool
    missing: list[str]
    available: list[str]


def check_tools(tools: list[str]) -> ToolCheckResult:
    """Check if declared tools are available in PATH."""
    from joshua.config_schema import KNOWN_TOOLS
    missing = []
    available = []
    for tool in tools:
        candidates = KNOWN_TOOLS.get(tool, [tool])
        if not candidates:  # always available (empty list = built-in)
            available.append(tool)
            continue
        found = any(shutil.which(cmd.split()[0]) for cmd in candidates)
        if found:
            available.append(tool)
        else:
            missing.append(tool)
    return ToolCheckResult(ok=len(missing) == 0, missing=missing, available=available)
