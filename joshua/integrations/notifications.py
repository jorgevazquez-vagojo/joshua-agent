"""Notification backends: Telegram, Slack, Discord, Webhook."""

import json
import logging
import threading
import time
import urllib.request
from abc import ABC, abstractmethod

log = logging.getLogger("joshua")

# Max failures before a notifier is disabled (circuit breaker)
DEFAULT_FAILURES_BEFORE_DISABLE = 5


class Notifier(ABC):
    """Abstract notification backend with circuit breaker and async dispatch."""

    def __init__(self):
        self._failures = 0
        self._disabled = False
        self._failures_before_disable = DEFAULT_FAILURES_BEFORE_DISABLE

    @abstractmethod
    def _send(self, text: str, agent_name: str = "", silent: bool = False):
        """Internal send — implemented by each backend."""
        ...

    def notify(self, text: str, agent_name: str = "", silent: bool = False):
        """Send notification in a background thread (non-blocking).

        Circuit breaker: disables notifier after N consecutive failures.
        """
        if self._disabled:
            return

        def _dispatch():
            try:
                self._send(text, agent_name, silent)
                self._failures = 0  # reset on success
            except Exception as e:
                self._failures += 1
                log.warning(f"Notification failed ({self._failures}/{self._failures_before_disable}): {e}")
                if self._failures >= self._failures_before_disable:
                    self._disabled = True
                    log.error(
                        f"Notifier disabled after {self._failures} consecutive failures. "
                        "Check your notification config."
                    )

        t = threading.Thread(target=_dispatch, daemon=True)
        t.start()

    def notify_event(self, event: str, details: str = "", project: str = ""):
        """Send a typed event notification."""
        prefix_map = {
            "start": "[START]",
            "stop": "[STOP]",
            "crash": "[CRASH]",
            "revert": "[REVERT]",
            "digest": "[DIGEST]",
            "health_fail": "[HEALTH]",
        }
        prefix = prefix_map.get(event, f"[{event.upper()}]")
        project_tag = f"({project}) " if project else ""
        self.notify(f"{prefix} {project_tag}{details}")


class TelegramNotifier(Notifier):
    """Send notifications via Telegram Bot API."""

    AGENT_LABELS = {
        "builder": "🔨",
        "debugger": "🔍",
        "qa": "🛡️",
    }

    def __init__(self, config: dict):
        super().__init__()
        self.token = config.get("token", "")
        self.chat_id = config.get("chat_id", "")
        self._failures_before_disable = config.get("failures_before_disable",
                                                    DEFAULT_FAILURES_BEFORE_DISABLE)
        if not self.token or not self.chat_id:
            log.warning("Telegram: token or chat_id missing, notifications disabled")
            self._disabled = True

    def _send(self, text: str, agent_name: str = "", silent: bool = False):
        label = self.AGENT_LABELS.get(agent_name, "📋")
        message = f"{label} {text}" if agent_name else text

        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }).encode()

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


class SlackNotifier(Notifier):
    """Send notifications via Slack Incoming Webhook."""

    def __init__(self, config: dict):
        super().__init__()
        self.webhook_url = config.get("webhook_url", "")
        self._failures_before_disable = config.get("failures_before_disable",
                                                    DEFAULT_FAILURES_BEFORE_DISABLE)
        if not self.webhook_url:
            log.warning("Slack: webhook_url missing, notifications disabled")
            self._disabled = True

    def _send(self, text: str, agent_name: str = "", silent: bool = False):
        payload = json.dumps(
            {"text": f"*{agent_name}*: {text}" if agent_name else text}
        ).encode()
        req = urllib.request.Request(self.webhook_url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


class WebhookNotifier(Notifier):
    """Send notifications to a generic HTTP endpoint."""

    def __init__(self, config: dict):
        super().__init__()
        self.url = config.get("url", "")
        self._failures_before_disable = config.get("failures_before_disable",
                                                    DEFAULT_FAILURES_BEFORE_DISABLE)

    def _send(self, text: str, agent_name: str = "", silent: bool = False):
        if not self.url:
            return
        payload = json.dumps({
            "text": text,
            "agent": agent_name,
            "silent": silent,
        }).encode()
        req = urllib.request.Request(self.url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


class NullNotifier(Notifier):
    """No-op notifier (notifications disabled)."""

    def _send(self, text: str, agent_name: str = "", silent: bool = False):
        pass


def notifier_factory(config: dict) -> Notifier:
    """Create a notifier from config."""
    notif_config = config.get("notifications", {})
    notif_type = notif_config.get("type", "none")

    if notif_type == "telegram":
        return TelegramNotifier(notif_config)
    elif notif_type == "slack":
        return SlackNotifier(notif_config)
    elif notif_type == "webhook":
        return WebhookNotifier(notif_config)
    else:
        return NullNotifier()
