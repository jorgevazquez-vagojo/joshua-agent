"""Sprint execution tracer — generates structured trace trees per cycle."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

NodeType = Literal["sprint", "cycle", "agent", "gate", "tool_call", "handoff"]

@dataclass
class TraceNode:
    id: str
    type: NodeType
    name: str
    status: str = "pending"   # pending | running | done | error | killed
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    input_preview: str = ""   # first 200 chars of input
    output_preview: str = ""  # first 200 chars of output
    metadata: dict = field(default_factory=dict)
    children: list["TraceNode"] = field(default_factory=list)

    def finish(self, status: str = "done", output: str = "") -> None:
        self.ended_at = time.time()
        self.duration_ms = int((self.ended_at - self.started_at) * 1000)
        self.status = status
        if output:
            self.output_preview = output[:200]

    def add_child(self, node: "TraceNode") -> "TraceNode":
        self.children.append(node)
        return node

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class CycleTracer:
    """Traces a single sprint cycle."""

    def __init__(self, sprint_id: str, cycle: int, project_dir: str | Path):
        self.sprint_id = sprint_id
        self.cycle = cycle
        self.project_dir = Path(project_dir)
        self.root = TraceNode(
            id=f"{sprint_id}-cycle-{cycle}",
            type="cycle",
            name=f"Cycle {cycle}",
        )
        self._agents: dict[str, TraceNode] = {}

    def start_agent(self, agent_name: str, task_preview: str = "") -> TraceNode:
        node = TraceNode(
            id=f"{self.sprint_id}-cycle-{self.cycle}-{agent_name}",
            type="agent",
            name=agent_name,
            status="running",
            input_preview=task_preview[:200],
        )
        self.root.add_child(node)
        self._agents[agent_name] = node
        return node

    def finish_agent(self, agent_name: str, run_result, status: str = "done") -> None:
        node = self._agents.get(agent_name)
        if not node:
            return
        output = ""
        if hasattr(run_result, "stdout"):
            output = (run_result.stdout or "")[:200]
        node.finish(status=status, output=output)
        node.tokens_out = getattr(run_result, "tokens_out", 0)
        if hasattr(run_result, "structured_output") and run_result.structured_output:
            node.metadata["structured_output"] = run_result.structured_output
        if getattr(run_result, "killed_by_token_limit", False):
            node.status = "killed"
            node.metadata["killed_reason"] = "token_limit"

    def add_tool_call(self, agent_name: str, tool: str, input_str: str, output_str: str, duration_ms: int = 0) -> None:
        parent = self._agents.get(agent_name)
        if not parent:
            return
        node = TraceNode(
            id=f"{self.sprint_id}-cycle-{self.cycle}-{agent_name}-{tool}-{len(parent.children)}",
            type="tool_call",
            name=tool,
            status="done",
            duration_ms=duration_ms,
            input_preview=input_str[:200],
            output_preview=output_str[:200],
        )
        node.ended_at = node.started_at
        parent.add_child(node)

    def start_gate(self, task_preview: str = "") -> TraceNode:
        return self.start_agent("gate", task_preview)

    def finish_gate(self, run_result, verdict: str, confidence: float, effort_score: int) -> None:
        self.finish_agent("gate", run_result)
        gate_node = self._agents.get("gate")
        if gate_node:
            gate_node.type = "gate"
            gate_node.metadata.update({
                "verdict": verdict,
                "confidence": confidence,
                "effort_score": effort_score,
            })

    def finish_cycle(self, verdict: str, confidence: float) -> None:
        self.root.finish(status="done")
        self.root.metadata["verdict"] = verdict
        self.root.metadata["confidence"] = confidence

    def save(self) -> Path:
        """Save trace to .joshua/traces/cycle-{N}.json"""
        traces_dir = self.project_dir / ".joshua" / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        path = traces_dir / f"cycle-{self.cycle}.json"
        path.write_text(json.dumps(self.root.to_dict(), indent=2, default=str))
        return path

    @classmethod
    def load(cls, project_dir: str | Path, cycle: int) -> dict | None:
        path = Path(project_dir) / ".joshua" / "traces" / f"cycle-{cycle}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    @classmethod
    def list_cycles(cls, project_dir: str | Path) -> list[int]:
        traces_dir = Path(project_dir) / ".joshua" / "traces"
        if not traces_dir.exists():
            return []
        cycles = []
        for f in traces_dir.glob("cycle-*.json"):
            try:
                cycles.append(int(f.stem.split("-")[1]))
            except (ValueError, IndexError):
                pass
        return sorted(cycles)
