"""URL validation to prevent SSRF attacks."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def validate_url(url: str, *, require_https: bool = False) -> str:
    """Validate a URL is safe to request (no private/internal IPs).

    Raises ValueError if the URL resolves to a private, loopback,
    link-local, or otherwise non-public address.
    """
    parsed = urlparse(url)
    allowed_schemes = ("https",) if require_https else ("http", "https")
    if parsed.scheme not in allowed_schemes:
        raise ValueError(f"URL scheme must be one of {allowed_schemes}, got '{parsed.scheme}'")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname")

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {parsed.hostname}")

    for _, _, _, _, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"URL resolves to non-public address ({ip})")

    return url
