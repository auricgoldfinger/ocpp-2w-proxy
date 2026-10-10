"""The optional [debug] section: a local HTTP endpoint that sends raw commands to a charger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config_error import ConfigError
from .config_values import flag, number, reject_unknown_keys

DEBUG_KEYS = frozenset({"enabled", "listen", "port", "default_timeout", "max_timeout"})


@dataclass(frozen=True)
class DebugConfig:
    enabled: bool = False
    # Loopback by default: the endpoint has no authentication.
    listen: str = "127.0.0.1"
    port: int = 8322
    default_timeout: float = 30
    # Stays below the 300 s lifetime of a route in the command router.
    max_timeout: float = 250


def parse_debug(section: Any, proxy_port: int) -> DebugConfig:
    if not isinstance(section, Mapping):
        raise ConfigError("[debug] must be a table")
    reject_unknown_keys(section, DEBUG_KEYS, "[debug]")
    max_timeout = number(section, "max_timeout", 250, "[debug]", float, 1)
    debug = DebugConfig(
        enabled=flag(section, "enabled", "[debug]"),
        listen=str(section.get("listen", "127.0.0.1")),
        port=number(section, "port", 8322, "[debug]", int, 0, 65535),
        default_timeout=number(section, "default_timeout", 30, "[debug]", float, 1, max_timeout),
        max_timeout=max_timeout,
    )
    if debug.port and debug.port == proxy_port:
        raise ConfigError(f"[debug] port {debug.port} is the charger-facing [proxy] port; choose another")
    return debug
