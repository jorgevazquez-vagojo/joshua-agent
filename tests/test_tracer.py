"""Tests for v1.15.0 trace viewer."""
import json
import pytest
from pathlib import Path
from joshua.utils.tracer import CycleTracer, TraceNode
from click.testing import CliRunner
from joshua.cli import main


def test_trace_node_finish():
    node = TraceNode(id="test", type="agent", name="dev")
    node.finish(status="done", output="hello world")
    assert node.status == "done"
    assert node.duration_ms >= 0
    assert node.output_preview == "hello world"


def test_cycle_tracer_save_load(tmp_path):
    tracer = CycleTracer("my-sprint", 3, tmp_path)

    # Simulate agent run
    tracer.start_agent("dev", "fix the bug")

    class FakeResult:
        stdout = "fixed it"
        tokens_out = 500
        structured_output = {"status": "success", "summary": "Fixed auth bug"}
        killed_by_token_limit = False

    tracer.finish_agent("dev", FakeResult())
    tracer.finish_cycle("GO", 0.95)
    path = tracer.save()

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["name"] == "Cycle 3"
    assert data["metadata"]["verdict"] == "GO"
    assert len(data["children"]) == 1
    assert data["children"][0]["name"] == "dev"


def test_cycle_tracer_list_cycles(tmp_path):
    for i in [1, 2, 5]:
        t = CycleTracer("sprint", i, tmp_path)
        t.finish_cycle("GO", 0.9)
        t.save()

    cycles = CycleTracer.list_cycles(tmp_path)
    assert cycles == [1, 2, 5]


def test_cycle_tracer_load_missing(tmp_path):
    result = CycleTracer.load(tmp_path, 99)
    assert result is None


def test_trace_show_no_traces(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["trace", "show", str(tmp_path)])
    assert result.exit_code != 0 or "no trace" in result.output.lower()


def test_trace_show_with_trace(tmp_path):
    tracer = CycleTracer("my-sprint", 1, tmp_path)
    tracer.start_agent("dev", "task")

    class FakeResult:
        stdout = "done"
        tokens_out = 100
        structured_output = None
        killed_by_token_limit = False

    tracer.finish_agent("dev", FakeResult())
    tracer.finish_cycle("GO", 0.88)
    tracer.save()

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "show", str(tmp_path), "--cycle", "1"])
    assert result.exit_code == 0
    assert "dev" in result.output or "Cycle" in result.output


def test_trace_list(tmp_path):
    for i in [1, 2]:
        t = CycleTracer("sprint", i, tmp_path)
        t.finish_cycle("GO" if i == 1 else "CAUTION", 0.9)
        t.save()

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "list", str(tmp_path)])
    assert result.exit_code == 0


def test_add_tool_call(tmp_path):
    tracer = CycleTracer("sprint", 1, tmp_path)
    tracer.start_agent("dev", "task")
    tracer.add_tool_call("dev", "run_tests", "pytest", "12 passed", 8200)

    class FakeResult:
        stdout = ""
        tokens_out = 0
        structured_output = None
        killed_by_token_limit = False

    tracer.finish_agent("dev", FakeResult())
    tracer.finish_cycle("GO", 0.9)
    tracer.save()

    data = CycleTracer.load(tmp_path, 1)
    assert data is not None
    dev_node = data["children"][0]
    assert len(dev_node["children"]) == 1
    assert dev_node["children"][0]["name"] == "run_tests"
