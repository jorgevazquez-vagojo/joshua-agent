"""Brain integration — bidirectional sync between Joshua sprints and Brain.

After each cycle:
  - POSTs cycle results to Brain's callback endpoint
  - POSTs extracted lessons as knowledge entries

Before each cycle:
  - Fetches relevant knowledge from Brain's scoped search
"""

import logging
import time

import requests

log = logging.getLogger("joshua")


class BrainCallback:
    """Sends cycle results and lessons to Brain's API."""

    def __init__(self, api_url: str, group_id: str, token: str = ""):
        self.api_url = api_url.rstrip("/")
        self.group_id = group_id
        self.token = token

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Internal-Token"] = self.token
        return h

    def on_cycle_complete(self, cycle_data: dict):
        """Called after each sprint cycle. POSTs results to Brain."""
        payload = {
            "group_id": self.group_id,
            **cycle_data,
        }
        try:
            resp = requests.post(
                f"{self.api_url}/api/sprints/callback",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code < 300:
                log.info(f"Brain callback OK — cycle {cycle_data.get('cycle')}")
            else:
                log.warning(f"Brain callback {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            log.warning(f"Brain callback failed: {e}")

    def post_knowledge(self, title: str, content: str, entry_type: str = "review_learning",
                       department: str = "", source: str = "joshua_sprint"):
        """Post a knowledge entry to Brain."""
        payload = {
            "title": title,
            "content": content,
            "entry_type": entry_type,
            "source": source,
            "department": department,
            "metadata": {"group_id": self.group_id},
        }
        try:
            requests.post(
                f"{self.api_url}/api/knowledge",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
        except Exception as e:
            log.warning(f"Brain knowledge POST failed: {e}")


class BrainContextProvider:
    """Fetches knowledge from Brain to inject into agent prompts.

    Caches results for `cache_ttl` seconds to avoid hammering Brain on every cycle.
    """

    def __init__(self, api_url: str, group_id: str, department: str = "",
                 token: str = "", cache_ttl: int = 300):
        self.api_url = api_url.rstrip("/")
        self.group_id = group_id
        self.department = department
        self.token = token
        self.cache_ttl = cache_ttl
        self._cache: str = ""
        self._cache_time: float = 0

    def _headers(self) -> dict:
        h = {}
        if self.token:
            h["X-Internal-Token"] = self.token
        return h

    def get_context(self, cycle: int) -> str:
        """Fetch knowledge from Brain, with caching."""
        now = time.monotonic()
        if self._cache and (now - self._cache_time) < self.cache_ttl:
            return self._cache

        try:
            resp = requests.get(
                f"{self.api_url}/api/knowledge/scoped-search",
                params={
                    "q": f"cycle {cycle}",
                    "department": self.department,
                    "group_ids": self.group_id,
                    "limit": 10,
                },
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                entries = resp.json()
                if isinstance(entries, list) and entries:
                    lines = ["\n--- BRAIN KNOWLEDGE ---"]
                    for e in entries[:10]:
                        title = e.get("title", "")
                        content = e.get("content", "")[:500]
                        lines.append(f"• {title}: {content}")
                    self._cache = "\n".join(lines)
                    self._cache_time = now
                    return self._cache
        except Exception as e:
            log.warning(f"Brain context fetch failed: {e}")

        return self._cache or ""


def setup_brain_integration(sprint, config: dict):
    """Wire Brain callback and context provider into a Sprint instance.

    Called by the server when a sprint config includes brain integration settings.

    Config format:
        integrations:
          brain:
            enabled: true
            api_url: "http://127.0.0.1:4000"
            api_token: "secret"
            group_id: "uuid"
            department: "engineering"
    """
    brain_conf = config.get("integrations", {}).get("brain", {})
    if not brain_conf.get("enabled"):
        return

    api_url = brain_conf.get("api_url", "http://127.0.0.1:4000")
    group_id = brain_conf.get("group_id", "")
    token = brain_conf.get("api_token", "")
    department = brain_conf.get("department", "")

    if not group_id:
        log.warning("Brain integration enabled but no group_id — skipping")
        return

    callback = BrainCallback(api_url, group_id, token)
    provider = BrainContextProvider(api_url, group_id, department, token)

    sprint.on_cycle_complete = callback.on_cycle_complete
    sprint.context_provider = provider.get_context

    log.info(f"Brain integration active — group={group_id}, api={api_url}")
