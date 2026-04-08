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


class GateTaskSource(TaskSource):
    """Generate tasks based on the last gate verdict from checkpoint.json.

    When the previous gate verdict was REVERT or CAUTION, prioritizes the
    top issue from gate findings. Falls back to a generic task otherwise.

    Config:
      project_dir: path to the project (injected automatically by sprint.py)
      state_dir:   override for .joshua state directory (optional)
      fallback_task: task to use when gate says GO (optional)
    """

    def __init__(self, config: dict):
        self.project_dir = config.get("project_dir", "")
        self.state_dir = config.get("state_dir", "")
        self.fallback_task = config.get(
            "fallback_task",
            "Review the codebase and identify any quality, performance, or reliability improvements.",
        )

    def _checkpoint_path(self):
        import os
        if self.state_dir:
            return os.path.join(self.state_dir, "checkpoint.json")
        return os.path.join(self.project_dir, ".joshua", "checkpoint.json")

    def _load_checkpoint(self) -> dict:
        import os
        path = self._checkpoint_path()
        if not os.path.exists(path):
            return {}
        try:
            return json.loads(open(path).read())
        except Exception:
            return {}

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        cp = self._load_checkpoint()
        if not cp:
            return None

        severity = cp.get("last_gate_severity", "none")
        findings = cp.get("last_gate_findings", "")
        last_cycle = cp.get("cycle", 0)

        # Only act on findings from the immediately preceding cycle
        if last_cycle != cycle - 1:
            return None

        if severity in ("critical", "high") or not severity or severity == "none":
            # Critical/high or no prior data → resolve top issue if findings exist
            if findings and severity in ("critical", "high"):
                # Extract first meaningful line as the top issue
                top_issue = next(
                    (ln.strip() for ln in findings.split("\n") if len(ln.strip()) > 20),
                    findings[:200],
                )
                task = f"Resolve the following issue found in the previous gate review:\n\n{top_issue}"
                log.info(f"[{agent_name}] GateTaskSource: severity={severity} → resolving top issue")
                return TaskFetchResult(
                    task=task,
                    source_id=f"gate-cycle-{last_cycle}",
                    metadata={"severity": severity, "gate_cycle": last_cycle},
                )

        if severity in ("medium", "low", "unknown"):
            if findings:
                top_issue = next(
                    (ln.strip() for ln in findings.split("\n") if len(ln.strip()) > 20),
                    findings[:200],
                )
                task = f"Address the following concern found in the previous gate review:\n\n{top_issue}"
                log.info(f"[{agent_name}] GateTaskSource: severity={severity} → addressing concern")
                return TaskFetchResult(
                    task=task,
                    source_id=f"gate-cycle-{last_cycle}",
                    metadata={"severity": severity, "gate_cycle": last_cycle},
                )

        # GO verdict or no actionable findings → use fallback
        return None


class GitHubTaskSource(TaskSource):
    """Fetch open issues from a GitHub repository.

    Config:
      repo:    owner/repo  (e.g. "acme/backend")
      token:   GitHub personal access token (optional — for private repos / higher rate limit)
      labels:  comma-separated label filter (e.g. "bug,help wanted")  optional
      state:   "open" (default) | "closed" | "all"
      max_results: max issues to consider per cycle (default 20)

    Example YAML:
      task_source: github
      task_source_config:
        repo: acme/backend
        token: ${GITHUB_TOKEN}
        labels: "bug"
    """

    _API_BASE = "https://api.github.com"

    def __init__(self, config: dict):
        self.repo = config.get("repo", "").strip("/")
        self.token = config.get("token", "")
        self.labels = config.get("labels", "")
        self.state = config.get("state", "open")
        self.max_results = int(config.get("max_results", 20))
        if not self.repo:
            raise ValueError("GitHubTaskSource requires 'repo' (owner/repo)")

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _fetch_issues(self) -> list[dict]:
        params = f"state={self.state}&per_page={self.max_results}"
        if self.labels:
            params += f"&labels={urllib.parse.quote(self.labels)}"
        url = f"{self._API_BASE}/repos/{self.repo}/issues?{params}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                issues = json.loads(resp.read())
                # GitHub issues API returns PRs too — filter them out
                return [i for i in issues if "pull_request" not in i]
        except Exception as e:
            log.warning(f"GitHubTaskSource: fetch failed for {self.repo}: {e}")
            return []

    def has_tasks(self) -> bool:
        return len(self._fetch_issues()) > 0

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        issues = self._fetch_issues()
        if not issues:
            log.info(f"[{agent_name}] No GitHub issues found in {self.repo}, using static fallback")
            return None

        issue = issues[(cycle - 1) % len(issues)]
        number = issue.get("number", "")
        title = issue.get("title", "")
        body = (issue.get("body") or "")[:3000]
        labels = ", ".join(lb["name"] for lb in issue.get("labels", []))

        parts = [f"GitHub #{number}: {title}"]
        if labels:
            parts.append(f"Labels: {labels}")
        if body.strip():
            parts.append(f"\nDescription:\n{body}")

        task = "\n".join(parts)
        log.info(f"[{agent_name}] GitHub issue fetched: #{number} — {title[:60]}")

        return TaskFetchResult(
            task=task,
            source_id=f"gh-{number}",
            metadata={"number": number, "title": title, "labels": labels},
        )


class NullTaskSource(TaskSource):
    """No-op — always returns None (use static tasks)."""

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        return None

    def has_tasks(self) -> bool:
        return False


class WebhookTaskSource(TaskSource):
    """Tasks delivered via HTTP POST and stored in .joshua/webhook_tasks.json.

    Use with POST /webhook/task endpoint on the joshua server.
    """

    def __init__(self, config: dict):
        self.project_dir = config.get("project_dir", "")
        self.state_dir = config.get("state_dir", "")

    def _tasks_path(self):
        from pathlib import Path
        if self.state_dir:
            return Path(self.state_dir) / "webhook_tasks.json"
        return Path(self.project_dir) / ".joshua" / "webhook_tasks.json"

    def _load(self) -> list[str]:
        p = self._tasks_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text())
        except Exception:
            return []

    def _save(self, tasks: list[str]):
        p = self._tasks_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(tasks))

    def has_tasks(self) -> bool:
        return len(self._load()) > 0

    def get_task(self, agent_name: str, cycle: int) -> TaskFetchResult | None:
        tasks = self._load()
        if not tasks:
            return None
        task = tasks.pop(0)
        self._save(tasks)
        log.info(f"[{agent_name}] Webhook task dequeued: {task[:80]}")
        return TaskFetchResult(task=task, source_id=f"webhook-cycle-{cycle}")


def task_source_factory(source_type: str, config: dict) -> TaskSource:
    """Create a TaskSource from config."""
    if source_type == "jira":
        return JiraTaskSource(config)
    if source_type == "gate":
        return GateTaskSource(config)
    if source_type == "github":
        return GitHubTaskSource(config)
    if source_type == "webhook":
        return WebhookTaskSource(config)
    return NullTaskSource()
