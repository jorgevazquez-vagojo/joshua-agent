"""Webhook notifiers for Slack, Discord, and Microsoft Teams."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("joshua")

_VERDICT_EMOJI = {
    "GO": "✅",
    "CAUTION": "⚠️",
    "REVERT": "❌",
}

_VERDICT_COLOR = {
    "GO": "#36a64f",       # green
    "CAUTION": "#ffa500",  # orange
    "REVERT": "#cc0000",   # red
}


def _post_json(url: str, payload: dict, headers: dict | None = None) -> bool:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning(f"Webhook notification failed ({url[:40]}...): {e}")
        return False


class SlackNotifier:
    """Post sprint verdict to a Slack Incoming Webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def notify(self, verdict: str, project: str, cycle: int, confidence: float,
               findings: str = "", branch: str = "") -> bool:
        emoji = _VERDICT_EMOJI.get(verdict.upper(), "❓")
        color = _VERDICT_COLOR.get(verdict.upper(), "#888888")
        conf_pct = f"{int(confidence * 100)}%" if confidence <= 1 else f"{int(confidence)}%"

        fields = [
            {"title": "Verdict", "value": f"{emoji} {verdict.upper()}", "short": True},
            {"title": "Confidence", "value": conf_pct, "short": True},
            {"title": "Cycle", "value": str(cycle), "short": True},
        ]
        if branch:
            fields.append({"title": "Branch", "value": branch, "short": True})

        text = f"*Joshua QA* — {project}"
        if findings and verdict.upper() != "GO":
            findings_short = findings[:300] + ("..." if len(findings) > 300 else "")
            fields.append({"title": "Findings", "value": findings_short, "short": False})

        payload = {
            "text": text,
            "attachments": [{
                "color": color,
                "fields": fields,
                "footer": "joshua-agent",
                "fallback": f"{project}: {verdict.upper()} (cycle {cycle})",
            }],
        }
        return _post_json(self.webhook_url, payload)


class DiscordNotifier:
    """Post sprint verdict to a Discord webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def notify(self, verdict: str, project: str, cycle: int, confidence: float,
               findings: str = "", branch: str = "") -> bool:
        emoji = _VERDICT_EMOJI.get(verdict.upper(), "❓")
        color_int = int(_VERDICT_COLOR.get(verdict.upper(), "#888888").lstrip("#"), 16)
        conf_pct = f"{int(confidence * 100)}%" if confidence <= 1 else f"{int(confidence)}%"

        desc_parts = [
            f"**Verdict:** {emoji} {verdict.upper()}",
            f"**Confidence:** {conf_pct}",
            f"**Cycle:** {cycle}",
        ]
        if branch:
            desc_parts.append(f"**Branch:** `{branch}`")
        if findings and verdict.upper() != "GO":
            short = findings[:400] + ("..." if len(findings) > 400 else "")
            desc_parts.append(f"\n**Findings:**\n```{short}```")

        payload = {
            "embeds": [{
                "title": f"Joshua QA — {project}",
                "description": "\n".join(desc_parts),
                "color": color_int,
                "footer": {"text": "joshua-agent"},
            }]
        }
        return _post_json(self.webhook_url, payload)


class TeamsNotifier:
    """Post sprint verdict to a Microsoft Teams Incoming Webhook (Adaptive Card)."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def notify(self, verdict: str, project: str, cycle: int, confidence: float,
               findings: str = "", branch: str = "") -> bool:
        emoji = _VERDICT_EMOJI.get(verdict.upper(), "❓")
        conf_pct = f"{int(confidence * 100)}%" if confidence <= 1 else f"{int(confidence)}%"
        color = {"GO": "Good", "CAUTION": "Warning", "REVERT": "Attention"}.get(
            verdict.upper(), "Default"
        )

        facts = [
            {"name": "Verdict", "value": f"{emoji} {verdict.upper()}"},
            {"name": "Confidence", "value": conf_pct},
            {"name": "Cycle", "value": str(cycle)},
        ]
        if branch:
            facts.append({"name": "Branch", "value": branch})

        sections = [{
            "activityTitle": f"**Joshua QA — {project}**",
            "activitySubtitle": f"Sprint cycle {cycle} complete",
            "facts": facts,
            "markdown": True,
        }]
        if findings and verdict.upper() != "GO":
            short = findings[:500] + ("..." if len(findings) > 500 else "")
            sections.append({
                "activityTitle": "Gate Findings",
                "text": short,
            })

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _VERDICT_COLOR.get(verdict.upper(), "#888888").lstrip("#"),
            "summary": f"{project}: {verdict.upper()}",
            "sections": sections,
        }
        return _post_json(self.webhook_url, payload)


def notify_all(config: dict, verdict: str, project: str, cycle: int,
               confidence: float, findings: str = "", branch: str = "") -> None:
    """Fire all configured webhook notifiers. Silently skips unconfigured ones."""
    notif_cfg = config.get("notifications", {})

    slack_url = notif_cfg.get("slack", "")
    discord_url = notif_cfg.get("discord", "")
    teams_url = notif_cfg.get("teams", "")

    if slack_url:
        ok = SlackNotifier(slack_url).notify(
            verdict, project, cycle, confidence, findings, branch
        )
        if ok:
            log.info(f"Slack notification sent: {verdict}")

    if discord_url:
        ok = DiscordNotifier(discord_url).notify(
            verdict, project, cycle, confidence, findings, branch
        )
        if ok:
            log.info(f"Discord notification sent: {verdict}")

    if teams_url:
        ok = TeamsNotifier(teams_url).notify(
            verdict, project, cycle, confidence, findings, branch
        )
        if ok:
            log.info(f"Teams notification sent: {verdict}")
