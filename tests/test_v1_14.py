"""Tests for v1.14.0: scratchpad, typed output, structured handoffs, tool check, token interrupts."""
import json
import pytest
from pathlib import Path
from joshua.utils.scratchpad import read_scratchpad, write_scratchpad, clear_scratchpad, scratchpad_summary
from joshua.utils.handoff import HandoffContext
from joshua.utils.tool_check import check_tools
from joshua.config_schema import AgentConfig


# ---------------------------------------------------------------------------
# Scratchpad tests
# ---------------------------------------------------------------------------

def test_scratchpad_empty(tmp_path):
    assert read_scratchpad(tmp_path) == {}


def test_scratchpad_write_read(tmp_path):
    write_scratchpad(tmp_path, "dev", {"files_changed": ["auth.py"], "summary": "Added login"})
    data = read_scratchpad(tmp_path)
    assert "dev" in data
    assert data["dev"]["files_changed"] == ["auth.py"]


def test_scratchpad_merges_multiple_agents(tmp_path):
    write_scratchpad(tmp_path, "dev", {"summary": "impl done"})
    write_scratchpad(tmp_path, "qa", {"tests_added": "5"})
    data = read_scratchpad(tmp_path)
    assert "dev" in data
    assert "qa" in data


def test_scratchpad_written_at_timestamp(tmp_path):
    write_scratchpad(tmp_path, "dev", {"summary": "done"})
    data = read_scratchpad(tmp_path)
    assert "_written_at" in data["dev"]


def test_scratchpad_clear(tmp_path):
    write_scratchpad(tmp_path, "dev", {"summary": "done"})
    clear_scratchpad(tmp_path)
    assert read_scratchpad(tmp_path) == {}


def test_scratchpad_clear_nonexistent(tmp_path):
    # Should not raise
    clear_scratchpad(tmp_path)


def test_scratchpad_summary_empty(tmp_path):
    assert scratchpad_summary(tmp_path) == ""


def test_scratchpad_summary_with_data(tmp_path):
    write_scratchpad(tmp_path, "dev", {"summary": "Added auth", "files_changed": ["auth.py"]})
    summary = scratchpad_summary(tmp_path)
    assert "dev" in summary
    assert "auth.py" in summary


def test_scratchpad_summary_skips_underscore_keys(tmp_path):
    write_scratchpad(tmp_path, "dev", {"summary": "done"})
    summary = scratchpad_summary(tmp_path)
    assert "_written_at" not in summary


# ---------------------------------------------------------------------------
# Handoff tests
# ---------------------------------------------------------------------------

def test_handoff_empty():
    ctx = HandoffContext(cycle=1, project="test")
    assert ctx.to_prompt_section() == ""


def test_handoff_with_successful_agent():
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        exit_code: int = 0
        structured_output: dict = None

        def __post_init__(self):
            self.structured_output = {
                "summary": "Fixed bug",
                "files_changed": ["main.py"],
                "issues_found": [],
            }

    ctx = HandoffContext(cycle=2, project="test")
    ctx.add_agent_result("dev", FakeResult())
    prompt = ctx.to_prompt_section()
    assert "dev" in prompt
    assert "Fixed bug" in prompt
    assert "main.py" in prompt


def test_handoff_with_failed_agent():
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        exit_code: int = 1
        structured_output = None

    ctx = HandoffContext(cycle=3, project="test")
    ctx.add_agent_result("buggy", FakeResult())
    prompt = ctx.to_prompt_section()
    assert "buggy" in prompt
    assert "FAIL" in prompt


def test_handoff_multiple_agents():
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        exit_code: int = 0
        structured_output: dict = None

        def __post_init__(self):
            self.structured_output = {"summary": "done", "files_changed": [], "issues_found": []}

    ctx = HandoffContext(cycle=4, project="test")
    ctx.add_agent_result("dev", FakeResult())
    ctx.add_agent_result("qa", FakeResult())
    prompt = ctx.to_prompt_section()
    assert "dev" in prompt
    assert "qa" in prompt
    assert "Cycle 4" in prompt


def test_handoff_no_structured_output():
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        exit_code: int = 0
        structured_output = None

    ctx = HandoffContext(cycle=1, project="test")
    ctx.add_agent_result("dev", FakeResult())
    prompt = ctx.to_prompt_section()
    assert "(no summary)" in prompt


# ---------------------------------------------------------------------------
# Tool check tests
# ---------------------------------------------------------------------------

def test_tool_check_always_available():
    result = check_tools(["read_file"])
    assert result.ok
    assert "read_file" in result.available


def test_tool_check_git():
    result = check_tools(["git_diff"])
    # git should be available in most environments
    assert isinstance(result.ok, bool)


def test_tool_check_unknown_missing():
    result = check_tools(["__nonexistent_tool_xyz__"])
    assert not result.ok
    assert "__nonexistent_tool_xyz__" in result.missing


def test_tool_check_empty_list():
    result = check_tools([])
    assert result.ok
    assert result.missing == []
    assert result.available == []


def test_tool_check_mixed():
    result = check_tools(["read_file", "__definitely_missing_abc__"])
    assert not result.ok
    assert "read_file" in result.available
    assert "__definitely_missing_abc__" in result.missing


# ---------------------------------------------------------------------------
# AgentConfig tests (v1.14.0 new fields)
# ---------------------------------------------------------------------------

def test_agent_config_tools():
    agent = AgentConfig(skill="dev", tools=["git_diff", "run_tests"])
    assert "git_diff" in agent.tools
    assert "run_tests" in agent.tools


def test_agent_config_tools_default_empty():
    agent = AgentConfig(skill="dev")
    assert agent.tools == []


def test_agent_config_max_tokens():
    agent = AgentConfig(skill="dev", max_tokens_per_run=1000)
    assert agent.max_tokens_per_run == 1000


def test_agent_config_max_tokens_default():
    agent = AgentConfig(skill="dev")
    assert agent.max_tokens_per_run == 0


def test_agent_config_output_format_text():
    agent = AgentConfig(skill="dev", output_format="text")
    assert agent.output_format == "text"


def test_agent_config_output_format_json():
    agent = AgentConfig(skill="dev", output_format="json")
    assert agent.output_format == "json"


def test_agent_config_output_format_invalid():
    with pytest.raises(Exception):
        AgentConfig(skill="dev", output_format="xml")


def test_agent_config_output_schema():
    from joshua.config_schema import AgentOutputSchema
    schema = AgentOutputSchema(status="success", summary="all good", confidence=0.9)
    agent = AgentConfig(skill="dev", output_schema=schema)
    assert agent.output_schema is not None
    assert agent.output_schema.confidence == 0.9


# ---------------------------------------------------------------------------
# AgentOutputSchema tests
# ---------------------------------------------------------------------------

def test_output_schema_defaults():
    from joshua.config_schema import AgentOutputSchema
    schema = AgentOutputSchema()
    assert schema.status == "success"
    assert schema.summary == ""
    assert schema.files_changed == []
    assert schema.tests_passed is None
    assert schema.tests_count == 0
    assert schema.issues_found == []
    assert schema.confidence == 0.0


def test_output_schema_full():
    from joshua.config_schema import AgentOutputSchema
    schema = AgentOutputSchema(
        status="partial",
        summary="Half done",
        files_changed=["a.py", "b.py"],
        tests_passed=True,
        tests_count=5,
        issues_found=["rate limit bug"],
        confidence=0.75,
    )
    assert schema.status == "partial"
    assert len(schema.files_changed) == 2
    assert schema.tests_count == 5
