"""HMAC signing utilities for joshua audit trail."""
from __future__ import annotations

import hashlib
import hmac


def sign_entry(entry: str, key: str = "") -> str:
    """Return HMAC-SHA256 hex of entry, or '' if key is empty."""
    if not key:
        return ""
    return hmac.new(key.encode(), entry.encode(), hashlib.sha256).hexdigest()


def verify_entry(entry: str, signature: str, key: str = "") -> bool:
    """Return True if signature matches, or True if key is empty (unsigned)."""
    if not key:
        return True
    expected = sign_entry(entry, key)
    return hmac.compare_digest(expected, signature)
