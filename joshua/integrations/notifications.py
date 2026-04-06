"""Notification backends: Telegram, Slack, Discord, Webhook."""

import json
import logging
import urllib.request
from abc import ABC, abstractmethod

log = logging.getLogger("joshua")


class Notifier(ABC):
    """Abstract notification backend."""

    @abstractmethod
    def notify(self, text: str, agent_name: str = "", silent: bool = False):
        """Send a notification."""
        ...

    def notify_event(self, event: str, details: str = "", project: str = ""):
        """Send a typed event notification.

        Events: start, stop, crash, revert, digest, health_fail
        """
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
        self.token = config.get("token", "")
        self.chat_id = config.get("chat_id", "")
        if not self.token or not self.chat_id:
            log.warning("Telegram: token or chat_id missing, notifications disabled")

    def notify(self, text: str, agent_name: str = "", silent: bool = False):
        if not self.token or not self.chat_id:
            return

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
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.warning(f"Telegram notification failed: {e}")


class SlackNotifier(Notifier):
    """Send notifications via Slack Incoming Webhook."""

    def __init__(self, config: dict):
        self.webhook_url = config.get("webhook_url", "")
        if not self.webhook_url:
            log.warning("Slack: webhook_url missing, notifications disabled")

    def notify(self, text: str, agent_name: str = "", silent: bool = False):
        if not self.webhook_url:
            return

        payload = json.dumps({"text": f"*{agent_name}*: {text}" if agent_name else text}).encode()
        req = urllib.request.Request(self.webhook_url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.warning(f"Slack notification failed: {e}")


class WebhookNotifier(Notifier):
    """Send notifications to a generic HTTP endpoint."""

    def __init__(self, config: dict):
        self.url = config.get("url", "")

    def notify(self, text: str, agent_name: str = "", silent: bool = False):
        if not self.url:
            return

        payload = json.dumps({
            "text": text,
            "agent": agent_name,
            "silent": silent,
        }).encode()
        req = urllib.request.Request(self.url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.warning(f"Webhook notification failed: {e}")


class NullNotifier(Notifier):
    """No-op notifier (notifications disabled)."""

    def notify(self, text: str, agent_name: str = "", silent: bool = False):
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
