"""Issue tracker backends: Jira, GitHub Issues, Filesystem."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("joshua")


@dataclass
class TrackerTask:
    """A task from an issue tracker."""
    id: str
    summary: str
    description: str = ""
    task_type: str = "task"  # task, bug, feature
    metadata: dict = field(default_factory=dict)


class Tracker(ABC):
    """Abstract issue tracker."""

    @abstractmethod
    def create_issue(self, summary: str, description: str, **kwargs) -> str | None:
        """Create an issue. Returns issue ID/URL or None."""
        ...

    @abstractmethod
    def add_comment(self, issue_id: str, text: str):
        """Add a comment to an existing issue."""
        ...


class JiraTracker(Tracker):
    """Jira Cloud REST API tracker."""

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "").rstrip("/")
        self.user = config.get("user", "")
        self.token = config.get("token", "")
        self.project_key = config.get("project_key", "")
        self.parent_key = config.get("parent_key", "")
        self.issue_type = config.get("issue_type", "Task")

    def _auth_header(self) -> str:
        creds = base64.b64encode(f"{self.user}:{self.token}".encode()).decode()
        return f"Basic {creds}"

    def create_issue(self, summary: str, description: str, **kwargs) -> str | None:
        if not self.base_url or not self.project_key:
            return None

        fields = {
            "project": {"key": self.project_key},
            "summary": summary[:255],
            "description": {
                "version": 1,
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description[:30000]}],
                }],
            },
            "issuetype": {"name": kwargs.get("issue_type", self.issue_type)},
        }
        if self.parent_key:
            fields["parent"] = {"key": self.parent_key}

        payload = json.dumps({"fields": fields}).encode()

        url = f"{self.base_url}/rest/api/3/issue"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": self._auth_header(),
        })

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            key = data.get("key", "")
            log.info(f"Jira issue created: {key}")
            return key
        except Exception as e:
            log.warning(f"Jira create failed: {e}")
            return None

    def add_comment(self, issue_id: str, text: str):
        if not self.base_url:
            return

        payload = json.dumps({
            "body": {
                "version": 1,
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text[:30000]}],
                }],
            }
        }).encode()
        url = f"{self.base_url}/rest/api/3/issue/{issue_id}/comment"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": self._auth_header(),
        })

        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log.warning(f"Jira comment failed: {e}")


class GitHubTracker(Tracker):
    """GitHub Issues tracker via REST API."""

    def __init__(self, config: dict):
        self.repo = config.get("repo", "")  # "owner/repo"
        self.token = config.get("token", "")

    def create_issue(self, summary: str, description: str, **kwargs) -> str | None:
        if not self.repo or not self.token:
            return None

        payload = json.dumps({
            "title": summary,
            "body": description,
            "labels": kwargs.get("labels", []),
        }).encode()

        url = f"https://api.github.com/repos/{self.repo}/issues"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        })

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            url = data.get("html_url", "")
            log.info(f"GitHub issue created: {url}")
            return url
        except Exception as e:
            log.warning(f"GitHub create failed: {e}")
            return None

    def add_comment(self, issue_id: str, text: str):
        if not self.repo or not self.token:
            return

        # issue_id should be the issue number
        payload = json.dumps({"body": text}).encode()
        url = f"https://api.github.com/repos/{self.repo}/issues/{issue_id}/comments"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        })

        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log.warning(f"GitHub comment failed: {e}")


class FilesystemTracker(Tracker):
    """Simple filesystem-based tracker using markdown files."""

    def __init__(self, config: dict):
        self.dir = Path(config.get("dir", ".joshua/issues")).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)

    def create_issue(self, summary: str, description: str, **kwargs) -> str | None:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-{re.sub(r'[^a-z0-9-]+', '-', summary[:50].lower())}.md"
        path = self.dir / filename
        path.write_text(f"# {summary}\n\n{description}\n")
        return str(path)

    def add_comment(self, issue_id: str, text: str):
        path = Path(issue_id).resolve()
        if not str(path).startswith(str(self.dir.resolve())):
            log.warning(f"FilesystemTracker: rejected path outside tracker dir: {issue_id}")
            return
        if path.exists():
            with open(path, "a") as f:
                f.write(f"\n---\n{text}\n")

    def get_next_task(self) -> tuple[str, str] | None:
        """Pick next pending task (.md file). Returns (path, content) or None.

        Renames the file to .wip to mark it as in progress.
        """
        files = sorted(self.dir.glob("*.md"))
        # Skip files that are already .wip, .done, or .failed
        pending = [f for f in files if not any(
            f.name.endswith(ext) for ext in (".wip", ".done", ".failed")
        )]
        if not pending:
            return None
        task_file = pending[0]
        content = task_file.read_text()
        wip_path = task_file.with_suffix(".md.wip")
        task_file.rename(wip_path)
        return str(wip_path), content

    def complete_task(self, path: str):
        """Mark a .wip task as done."""
        p = Path(path)
        if p.exists():
            p.rename(p.with_name(p.name.replace(".wip", ".done")))

    def fail_task(self, path: str):
        """Mark a .wip task as failed."""
        p = Path(path)
        if p.exists():
            p.rename(p.with_name(p.name.replace(".wip", ".failed")))


class NullTracker(Tracker):
    """No-op tracker."""

    def create_issue(self, summary: str, description: str, **kwargs) -> str | None:
        return None

    def add_comment(self, issue_id: str, text: str):
        pass


def tracker_factory(config: dict) -> Tracker:
    """Create a tracker from config."""
    tracker_config = config.get("tracker", {})
    tracker_type = tracker_config.get("type", "none")

    if tracker_type == "jira":
        return JiraTracker(tracker_config)
    elif tracker_type == "github":
        return GitHubTracker(tracker_config)
    elif tracker_type == "filesystem":
        return FilesystemTracker(tracker_config)
    else:
        return NullTracker()
