"""Structured handoff context between agents in the same cycle."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class HandoffContext:
    previous_agents: list[dict] = field(default_factory=list)
    # [{name, status, summary, files_changed, issues_found}]
    cycle: int = 0
    project: str = ""

    def add_agent_result(self, name: str, run_result) -> None:
        """Add a completed agent's result to the handoff context."""
        entry = {
            "agent": name,
            "status": "success" if run_result.exit_code == 0 else "failed",
            "summary": "",
            "files_changed": [],
            "issues_found": [],
        }
        if run_result.structured_output:
            entry.update({
                "summary": run_result.structured_output.get("summary", ""),
                "files_changed": run_result.structured_output.get("files_changed", []),
                "issues_found": run_result.structured_output.get("issues_found", []),
            })
        self.previous_agents.append(entry)

    def to_prompt_section(self) -> str:
        """Format as a prompt section for the next agent."""
        if not self.previous_agents:
            return ""
        lines = [f"=== Sprint Cycle {self.cycle} — Agent Handoff Context ==="]
        for agent in self.previous_agents:
            status_icon = "OK" if agent["status"] == "success" else "FAIL"
            lines.append(f"\n[{status_icon}] {agent['agent']}: {agent['summary'] or '(no summary)'}")
            if agent["files_changed"]:
                lines.append(f"   Files changed: {', '.join(agent['files_changed'])}")
            if agent["issues_found"]:
                lines.append(f"   Issues found: {', '.join(agent['issues_found'])}")
        lines.append("\nBased on the above, focus your work accordingly.")
        return "\n".join(lines)
