"""The optional [monitor] section: a local, read-only web UI that shows the message flow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_error import ConfigError
from .config_values import flag, number, reject_unknown_keys

MONITOR_KEYS = frozenset({"enabled", "listen", "port", "db_path", "retention_days"})
DB_FILE_NAME = "monitor.sqlite"


@dataclass(frozen=True)
class MonitorConfig:
    enabled: bool = False
    # Loopback by default: the monitor has no authentication.
    listen: str = "127.0.0.1"
    port: int = 8323
    # None only before parsing; parse_monitor resolves it to <state_dir>/monitor.sqlite.
    db_path: Path | None = None
    retention_days: int = 90


def parse_monitor(section: Any, proxy_port: int, debug_port: int, state_dir: Path) -> MonitorConfig:
    if not isinstance(section, Mapping):
        raise ConfigError("[monitor] must be a table")
    reject_unknown_keys(section, MONITOR_KEYS, "[monitor]")
    monitor = MonitorConfig(
        enabled=flag(section, "enabled", "[monitor]"),
        listen=str(section.get("listen", "127.0.0.1")),
        port=number(section, "port", 8323, "[monitor]", int, 0, 65535),
        db_path=_db_path(section, state_dir),
        retention_days=number(section, "retention_days", 90, "[monitor]", int, 1),
    )
    _reject_port_clash(monitor.port, proxy_port, debug_port)
    return monitor


def _db_path(section: Mapping[str, Any], state_dir: Path) -> Path:
    value = section.get("db_path")
    if value is None:
        return state_dir / DB_FILE_NAME
    if not isinstance(value, str) or not value:
        raise ConfigError(f"[monitor]: db_path must be a file path, not {value!r}")
    return Path(value)


def _reject_port_clash(port: int, proxy_port: int, debug_port: int) -> None:
    if not port:
        return  # 0 picks a free port, so it cannot clash
    if port == proxy_port:
        raise ConfigError(f"[monitor] port {port} is the charger-facing [proxy] port; choose another")
    if port == debug_port:
        raise ConfigError(f"[monitor] port {port} is the [debug] port; choose another")
