"""Create tickets in Jira or Linear when a sprint returns REVERT."""
from __future__ import annotations
import json, logging, urllib.request, urllib.parse
log = logging.getLogger("joshua")

class JiraTicketSink:
    """Create a Jira issue on REVERT."""
    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "").rstrip("/")   # e.g. https://acme.atlassian.net
        self.email = config.get("email", "")
        self.token = config.get("token", "")
        self.project_key = config.get("project_key", "")         # e.g. ENG
        self.issue_type = config.get("issue_type", "Bug")
        if not all([self.base_url, self.email, self.token, self.project_key]):
            log.warning("JiraTicketSink: base_url, email, token, project_key required")

    def create(self, summary: str, description: str, labels: list[str] | None = None) -> str | None:
        """Create issue. Returns issue key (e.g. ENG-123) or None on failure."""
        import base64
        creds = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        payload = json.dumps({
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary[:255],
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description[:2000]}]}
                ]},
                "issuetype": {"name": self.issue_type},
                "labels": labels or ["joshua-qa", "auto-created"],
            }
        }).encode()
        url = f"{self.base_url}/rest/api/3/issue"
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                key = data.get("key", "")
                log.info(f"Jira issue created: {key}")
                return key
        except Exception as e:
            log.warning(f"Jira ticket creation failed: {e}")
            return None


class LinearTicketSink:
    """Create a Linear issue on REVERT via GraphQL API."""
    def __init__(self, config: dict):
        self.token = config.get("token", "")
        self.team_id = config.get("team_id", "")   # Linear team UUID
        if not all([self.token, self.team_id]):
            log.warning("LinearTicketSink: token and team_id required")

    def create(self, summary: str, description: str, labels: list[str] | None = None) -> str | None:
        mutation = """
        mutation CreateIssue($title: String!, $description: String!, $teamId: String!) {
          issueCreate(input: {title: $title, description: $description, teamId: $teamId}) {
            success
            issue { identifier url }
          }
        }
        """
        payload = json.dumps({
            "query": mutation,
            "variables": {"title": summary[:255], "description": description[:5000], "teamId": self.team_id}
        }).encode()
        req = urllib.request.Request("https://api.linear.app/graphql", data=payload, headers={
            "Authorization": self.token,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                issue = data.get("data", {}).get("issueCreate", {}).get("issue", {})
                identifier = issue.get("identifier", "")
                log.info(f"Linear issue created: {identifier}")
                return identifier
        except Exception as e:
            log.warning(f"Linear ticket creation failed: {e}")
            return None


def ticket_sink_factory(config: dict):
    """Create ticket sink from sprint config. Returns None if not configured."""
    sink_cfg = config.get("ticket_sink", {})
    provider = sink_cfg.get("type", "none")
    if provider == "jira":
        return JiraTicketSink(sink_cfg)
    if provider == "linear":
        return LinearTicketSink(sink_cfg)
    return None


def maybe_create_ticket(config: dict, verdict: str, project: str, cycle: int, findings: str) -> None:
    """Create a ticket if verdict is REVERT and ticket_sink is configured."""
    if verdict.upper() != "REVERT":
        return
    sink = ticket_sink_factory(config)
    if sink is None:
        return
    summary = f"[Joshua QA] REVERT — {project} cycle {cycle}"
    description = f"Joshua QA agent returned REVERT on {project} at cycle {cycle}.\n\nGate findings:\n{findings}"
    sink.create(summary, description)
