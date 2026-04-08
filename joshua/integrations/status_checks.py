"""Post sprint verdict as a commit status to GitHub or GitLab."""
from __future__ import annotations
import json
import logging
import urllib.request

log = logging.getLogger("joshua")


class GitHubStatusCheck:
    """Post verdict as GitHub commit status via API."""

    def __init__(self, config: dict):
        self.token = config.get("token", "")
        self.repo = config.get("repo", "")   # owner/repo
        self.sha = config.get("sha", "")     # commit SHA
        if not all([self.token, self.repo, self.sha]):
            log.warning("GitHubStatusCheck: token, repo, sha required")

    def post(self, verdict: str, description: str = "", context: str = "joshua/qa") -> bool:
        """Post verdict as commit status. verdict: GO|CAUTION|REVERT"""
        state_map = {"GO": "success", "CAUTION": "failure", "REVERT": "failure"}
        state = state_map.get(verdict.upper(), "error")
        payload = json.dumps({
            "state": state,
            "description": (description or f"Joshua QA: {verdict}")[:140],
            "context": context,
        }).encode()
        url = f"https://api.github.com/repos/{self.repo}/statuses/{self.sha}"
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })
        try:
            urllib.request.urlopen(req, timeout=10)
            log.info(f"GitHub status posted: {verdict} → {state} on {self.sha[:8]}")
            return True
        except Exception as e:
            log.warning(f"GitHub status check failed: {e}")
            return False


class GitLabStatusCheck:
    """Post verdict as GitLab commit status via API."""

    def __init__(self, config: dict):
        self.token = config.get("token", "")
        self.project_id = str(config.get("project_id", ""))   # numeric or "namespace/repo"
        self.sha = config.get("sha", "")
        self.base_url = config.get("base_url", "https://gitlab.com").rstrip("/")
        if not all([self.token, self.project_id, self.sha]):
            log.warning("GitLabStatusCheck: token, project_id, sha required")

    def post(self, verdict: str, description: str = "", name: str = "joshua/qa") -> bool:
        import urllib.parse
        state_map = {"GO": "success", "CAUTION": "failed", "REVERT": "failed"}
        state = state_map.get(verdict.upper(), "failed")
        encoded_id = urllib.parse.quote(self.project_id, safe="")
        payload = json.dumps({
            "state": state,
            "description": (description or f"Joshua QA: {verdict}")[:140],
            "name": name,
        }).encode()
        url = f"{self.base_url}/api/v4/projects/{encoded_id}/statuses/{self.sha}"
        req = urllib.request.Request(url, data=payload, headers={
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
        })
        try:
            urllib.request.urlopen(req, timeout=10)
            log.info(f"GitLab status posted: {verdict} → {state} on {self.sha[:8]}")
            return True
        except Exception as e:
            log.warning(f"GitLab status check failed: {e}")
            return False


def status_check_factory(config: dict):
    """Create a status check handler from sprint config."""
    sc_cfg = config.get("status_check", {})
    provider = sc_cfg.get("type", "none")
    if provider == "github":
        return GitHubStatusCheck(sc_cfg)
    if provider == "gitlab":
        return GitLabStatusCheck(sc_cfg)
    return None
