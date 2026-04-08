"""Dynamic task sources — fetch tasks from Jira, GitHub, or other systems.

Task sources are hooks that agents use to get tasks dynamically instead of
(or in addition to) the static task list in YAML config. When a task source
is bound to an agent, get_task() tries the source first and falls back to
the static list if the source returns None.

    agents:
      vulcan:
        skill: vulcan
        task_source: jira
        task_source_config:
          base_url: https://company.atlassian.net
          jql: "project = PROJ AND type = Bug AND resolution = Unresolved"
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("joshua")


@dataclass
class TaskFetchResult:
    """Result from a dynamic task source."""
    task: str
    source_id: str | None = None  # e.g., Jira issue key "PROJ-123"
    metadata: dict = field(default_factory=dict)


class TaskSource(ABC):
    """Abstract base for dynamic task sources."""

    @abstractmethod
    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        """Fetch next task for an agent. Return None to use static fallback."""
        ...

    def has_tasks(self) -> bool:
        """Quick poll: are there tasks available? Used by event trigger mode.
        Default: always True (assume work exists). Override for real polling."""
        return True


class JiraTaskSource(TaskSource):
    """Fetch tasks from Jira via JQL search."""

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "").rstrip("/")
        self.user = config.get("user", "")
        self.token = config.get("token", "")
        self.project_key = config.get("project_key", "")
        self.jql = config.get("jql", "")
        self.max_results = config.get("max_results", 10)
        # Enforce HTTPS for Jira connections (credentials are sent via Basic Auth)
        if self.base_url and not self.base_url.startswith("https://"):
            raise ValueError("Jira base_url must use HTTPS (credentials are sent via Basic Auth)")

    def _auth_header(self) -> str:
        creds = base64.b64encode(f"{self.user}:{self.token}".encode()).decode()
        return f"Basic {creds}"

    def _extract_text(self, node) -> str:
        """Extract plain text from Jira ADF (Atlassian Document Format)."""
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            if node.get("type") == "hardBreak":
                return "\n"
            return "".join(self._extract_text(c) for c in node.get("content", []))
        if isinstance(node, list):
            return "".join(self._extract_text(c) for c in node)
        return ""

    def _build_jql(self) -> str:
        """Build JQL query — use explicit jql or default from project_key."""
        if self.jql:
            return self.jql
        if self.project_key:
            return (
                f"project = {self.project_key} "
                f"AND resolution = Unresolved "
                f"ORDER BY priority DESC, created DESC"
            )
        return ""

    def search_issues(self) -> list[dict]:
        """Search Jira for issues matching JQL."""
        jql = self._build_jql()
        if not jql or not self.base_url:
            return []

        encoded_jql = urllib.parse.quote(jql)
        url = (
            f"{self.base_url}/rest/api/3/search/jql"
            f"?jql={encoded_jql}"
            f"&maxResults={self.max_results}"
            f"&fields=key,summary,description,issuetype,priority,status,comment"
        )
        req = urllib.request.Request(url, headers={
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data.get("issues", [])
        except Exception as e:
            log.warning(f"Jira task source search failed: {e}")
            return []

    def has_tasks(self) -> bool:
        """Lightweight poll — just checks if JQL returns any issues."""
        return len(self.search_issues()) > 0

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        """Fetch next task from Jira. Picks by cycle (round-robin over results)."""
        issues = self.search_issues()
        if not issues:
            log.info(f"[{agent_name}] No Jira tasks found, using static fallback")
            return None

        # Round-robin over available issues
        issue = issues[(cycle - 1) % len(issues)]
        fields = issue.get("fields", {})
        key = issue.get("key", "")
        summary = fields.get("summary", "")
        description_adf = fields.get("description")
        description = self._extract_text(description_adf) if description_adf else ""
        issue_type = fields.get("issuetype", {}).get("name", "")
        priority = fields.get("priority", {}).get("name", "")

        # Build task prompt from Jira issue
        parts = [f"Jira {key}: {summary}"]
        if priority:
            parts.append(f"Priority: {priority}")
        if issue_type:
            parts.append(f"Type: {issue_type}")
        if description:
            parts.append(f"\nDescription:\n{description[:3000]}")

        # Include recent comments as context
        comments = fields.get("comment", {}).get("comments", [])
        if comments:
            recent = comments[-3:]  # last 3 comments
            parts.append("\nRecent comments:")
            for c in recent:
                author = c.get("author", {}).get("displayName", "Unknown")
                text = self._extract_text(c.get("body", {}))
                if text.strip():
                    parts.append(f"  [{author}]: {text[:500]}")

        task = "\n".join(parts)
        log.info(f"[{agent_name}] Jira task fetched: {key} — {summary[:60]}")

        return TaskFetchResult(
            task=task,
            source_id=key,
            metadata={
                "issue_type": issue_type,
                "priority": priority,
                "summary": summary,
            },
        )


class NullTaskSource(TaskSource):
    """No-op — always returns None (use static tasks)."""

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        return None

    def has_tasks(self) -> bool:
        return False


def task_source_factory(source_type: str, config: dict) -> TaskSource:
    """Create a TaskSource from config."""
    if source_type == "jira":
        return JiraTaskSource(config)
    return NullTaskSource()
