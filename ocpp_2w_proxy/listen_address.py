"""Whether an unauthenticated listener stays on this host, and the warning when it does not."""

from __future__ import annotations

import ipaddress
import logging


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def warn_unless_loopback(logger: logging.Logger, host: str, endpoint: str, exposure: str) -> None:
    """Warn that `endpoint`, bound to `host`, is open beyond this host; `exposure` says what that allows."""
    if not is_loopback(host):
        logger.warning("%s is reachable beyond this host and has no authentication: %s", endpoint, exposure)
