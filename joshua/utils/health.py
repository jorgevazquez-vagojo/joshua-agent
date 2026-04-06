"""Health check utilities."""

import logging
import urllib.request

log = logging.getLogger("joshua")


def check_health(url: str, timeout: int = 10) -> bool:
    """Check if a service is healthy via HTTP GET.

    Returns True if status code is 2xx.
    """
    if not url:
        return True  # No health URL configured = skip check

    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        ok = 200 <= resp.status < 300
        if not ok:
            log.warning(f"Health check failed: {url} returned {resp.status}")
        return ok
    except Exception as e:
        log.warning(f"Health check failed: {url} - {e}")
        return False
