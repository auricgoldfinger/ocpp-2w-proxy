"""Crash-safe persistence of per-charger proxy state (one JSON file per charger)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class ChargerState:
    # primary transactionId (as str) -> secondary transactionId
    transactions: dict[str, int] = field(default_factory=dict)
    # StartTransaction message id -> transactionId, while waiting for the other backend's answer
    pending_primary_starts: dict[str, int] = field(default_factory=dict)
    pending_secondary_starts: dict[str, int] = field(default_factory=dict)
    # Durable calls not yet confirmed by the secondary backend, oldest first
    outbox: list[dict[str, Any]] = field(default_factory=list)
    # Last BootNotification payload and last StatusNotification payload per connector
    boot: dict[str, Any] | None = None
    statuses: dict[str, dict[str, Any]] = field(default_factory=dict)


class StateStore:
    """Owns one charger's state and writes it atomically after each change."""

    def __init__(self, path: Path):
        self.path = path
        self.state = self._load()

    @classmethod
    def for_charger(cls, state_dir: Path, charger_id: str) -> StateStore:
        return cls(state_dir / f"{charger_id}.json")

    def _load(self) -> ChargerState:
        if not self.path.exists():
            return ChargerState()
        try:
            data = json.loads(self.path.read_text())
            if data.get("version") != SCHEMA_VERSION:
                raise ValueError(f"unsupported state version {data.get('version')!r}")
            data.pop("version")
            return ChargerState(**data)
        except (OSError, ValueError, TypeError) as exc:
            # Never silently discard billing data: keep the unreadable file for inspection.
            backup = self.path.with_suffix(".corrupt")
            logger.error("state file %s unreadable (%s); moved to %s and starting empty", self.path, exc, backup)
            self.path.replace(backup)
            return ChargerState()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps({"version": SCHEMA_VERSION, **asdict(self.state)}, separators=(",", ":"))
        with tmp.open("w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(self.path)
