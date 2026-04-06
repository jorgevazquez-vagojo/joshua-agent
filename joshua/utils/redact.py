"""Helpers to redact secrets before logs or persistence."""

from __future__ import annotations

import re


_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-+/=]{8,})")

_ASSIGNMENT_PATTERNS = [
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\b"
        r"(\s*[:=]\s*|\s+is\s+)([^\s,;]+)"
    ),
]

_TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
    re.DOTALL,
)


def redact_secrets(text: str) -> str:
    """Replace likely credentials with stable placeholders."""
    if not text:
        return text

    redacted = _PEM_PATTERN.sub("[REDACTED PRIVATE KEY]", text)
    redacted = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} [REDACTED]", redacted)

    for pattern in _ASSIGNMENT_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", redacted)

    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED SECRET]", redacted)

    return redacted
