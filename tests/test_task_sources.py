"""Tests for dynamic task source hooks."""

import json
from unittest.mock import patch, MagicMock

import pytest

from joshua.integrations.task_sources import (
    TaskFetchResult,
    TaskSource,
    JiraTaskSource,
    NullTaskSource,
    task_source_factory,
)
from joshua.agents import Agent


# ── TaskFetchResult ────────────────────────────────────────────────────

class TestTaskFetchResult:
    def test_basic(self):
        r = TaskFetchResult(task="Fix bug", source_id="PROJ-1")
        assert r.task == "Fix bug"
        assert r.source_id == "PROJ-1"
        assert r.metadata == {}

    def test_with_metadata(self):
        r = TaskFetchResult(task="t", metadata={"priority": "High"})
        assert r.metadata["priority"] == "High"


# ── NullTaskSource ─────────────────────────────────────────────────────

class TestNullTaskSource:
    def test_always_none(self):
        source = NullTaskSource()
        assert source.get_task("dev", 1) is None
        assert source.get_task("vulcan", 99) is None


# ── JiraTaskSource ─────────────────────────────────────────────────────

class TestJiraTaskSource:
    def test_init(self):
        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "user@test.com",
            "token": "secret",
            "project_key": "TEST",
        })
        assert source.base_url == "https://test.atlassian.net"
        assert source.project_key == "TEST"

    def test_build_jql_explicit(self):
        source = JiraTaskSource({"jql": "project = X AND type = Bug"})
        assert source._build_jql() == "project = X AND type = Bug"

    def test_build_jql_from_project_key(self):
        source = JiraTaskSource({"project_key": "PROJ"})
        jql = source._build_jql()
        assert "project = PROJ" in jql
        assert "Unresolved" in jql

    def test_build_jql_empty(self):
        source = JiraTaskSource({})
        assert source._build_jql() == ""

    def test_extract_text_simple(self):
        source = JiraTaskSource({})
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}
            ],
        }
        assert source._extract_text(adf) == "Hello world"

    def test_extract_text_with_break(self):
        source = JiraTaskSource({})
        adf = {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Line 1"},
                {"type": "hardBreak"},
                {"type": "text", "text": "Line 2"},
            ],
        }
        assert source._extract_text(adf) == "Line 1\nLine 2"

    @patch("joshua.integrations.task_sources.urllib.request.urlopen")
    def test_search_issues(self, mock_urlopen):
        response_data = {
            "issues": [
                {
                    "key": "TEST-1",
                    "fields": {
                        "summary": "Fix login",
                        "description": None,
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "High"},
                        "status": {"name": "To Do"},
                    },
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "u",
            "token": "t",
            "project_key": "TEST",
        })
        issues = source.search_issues()
        assert len(issues) == 1
        assert issues[0]["key"] == "TEST-1"

    @patch("joshua.integrations.task_sources.urllib.request.urlopen")
    def test_get_task_success(self, mock_urlopen):
        response_data = {
            "issues": [
                {
                    "key": "PROJ-42",
                    "fields": {
                        "summary": "SQL injection in user endpoint",
                        "description": {
                            "type": "doc",
                            "content": [
                                {"type": "paragraph", "content": [
                                    {"type": "text", "text": "Parameterize queries"}
                                ]}
                            ],
                        },
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "Critical"},
                        "status": {"name": "Open"},
                        "comment": {"comments": []},
                    },
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "u",
            "token": "t",
            "project_key": "PROJ",
        })
        result = source.get_task("vulcan", cycle=1)
        assert result is not None
        assert "PROJ-42" in result.task
        assert "SQL injection" in result.task
        assert result.source_id == "PROJ-42"
        assert result.metadata["priority"] == "Critical"

    @patch("joshua.integrations.task_sources.urllib.request.urlopen")
    def test_get_task_no_results(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"issues": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "u",
            "token": "t",
            "jql": "project = EMPTY",
        })
        result = source.get_task("vulcan", cycle=1)
        assert result is None

    @patch("joshua.integrations.task_sources.urllib.request.urlopen", side_effect=Exception("network"))
    def test_get_task_network_error(self, mock_urlopen):
        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "u",
            "token": "t",
            "project_key": "PROJ",
        })
        result = source.get_task("vulcan", cycle=1)
        assert result is None

    @patch("joshua.integrations.task_sources.urllib.request.urlopen")
    def test_round_robin(self, mock_urlopen):
        response_data = {
            "issues": [
                {"key": "P-1", "fields": {"summary": "Task A", "description": None,
                    "issuetype": {"name": "Task"}, "priority": {"name": "Low"},
                    "comment": {"comments": []}}},
                {"key": "P-2", "fields": {"summary": "Task B", "description": None,
                    "issuetype": {"name": "Task"}, "priority": {"name": "Low"},
                    "comment": {"comments": []}}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        source = JiraTaskSource({
            "base_url": "https://test.atlassian.net",
            "user": "u", "token": "t", "project_key": "P",
        })
        r1 = source.get_task("dev", cycle=1)
        r2 = source.get_task("dev", cycle=2)
        r3 = source.get_task("dev", cycle=3)
        assert r1.source_id == "P-1"
        assert r2.source_id == "P-2"
        assert r3.source_id == "P-1"  # wraps around


# ── Factory ────────────────────────────────────────────────────────────

class TestFactory:
    def test_jira(self):
        source = task_source_factory("jira", {"base_url": "https://x.atlassian.net"})
        assert isinstance(source, JiraTaskSource)

    def test_unknown(self):
        source = task_source_factory("unknown", {})
        assert isinstance(source, NullTaskSource)

    def test_none(self):
        source = task_source_factory("", {})
        assert isinstance(source, NullTaskSource)


# ── Agent integration ──────────────────────────────────────────────────

class TestAgentTaskSourceHook:
    def test_static_fallback_no_source(self):
        agent = Agent(name="dev", skill="dev", tasks=["static task 1", "static task 2"])
        assert agent.get_task(1) == "static task 1"
        assert agent.get_task(2) == "static task 2"

    def test_dynamic_overrides_static(self):
        mock_source = MagicMock(spec=TaskSource)
        mock_source.get_task.return_value = TaskFetchResult(
            task="dynamic from Jira", source_id="PROJ-1"
        )
        agent = Agent(name="vulcan", skill="vulcan", tasks=["static"], task_source=mock_source)
        assert agent.get_task(1) == "dynamic from Jira"
        mock_source.get_task.assert_called_once_with("vulcan", 1)

    def test_fallback_when_source_returns_none(self):
        mock_source = MagicMock(spec=TaskSource)
        mock_source.get_task.return_value = None
        agent = Agent(name="dev", skill="dev", tasks=["fallback"], task_source=mock_source)
        assert agent.get_task(1) == "fallback"

    def test_fallback_when_source_raises(self):
        mock_source = MagicMock(spec=TaskSource)
        mock_source.get_task.side_effect = RuntimeError("boom")
        agent = Agent(name="dev", skill="dev", tasks=["safe"], task_source=mock_source)
        assert agent.get_task(1) == "safe"

    def test_generic_fallback_no_source_no_tasks(self):
        agent = Agent(name="dev", skill="dev")
        assert "General dev review" in agent.get_task(1)
