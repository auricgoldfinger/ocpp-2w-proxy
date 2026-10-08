"""Crash-safe persistence of per-charger proxy state (one JSON file per charger)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


@dataclass
class ChargerState:
    # primary transactionId (as str) -> {backend name: that backend's transactionId}
    transactions: dict[str, dict[str, int]] = field(default_factory=dict)
    # StartTransaction message id -> transactionId, while waiting for the backends' answers
    pending_primary_starts: dict[str, int] = field(default_factory=dict)
    # Backend name -> {StartTransaction message id: that backend's transactionId}
    pending_secondary_starts: dict[str, dict[str, int]] = field(default_factory=dict)
    # Primary calls acknowledged locally while the primary backend was unavailable
    primary_outbox: list[dict[str, Any]] = field(default_factory=list)
    # Backend name -> durable calls not yet confirmed by that backend, oldest first
    outboxes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
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
            backup = self._park_corrupt()
            logger.error("state file %s unreadable (%s); moved to %s and starting empty", self.path, exc, backup)
            return ChargerState()

    def _park_corrupt(self) -> Path:
        """Move the unreadable state file aside, never overwriting an earlier backup."""
        backup = self.path.with_suffix(".corrupt")
        suffix = 0
        while backup.exists():
            suffix += 1
            backup = self.path.with_suffix(f".corrupt.{suffix}")
        self.path.replace(backup)
        return backup

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps({"version": SCHEMA_VERSION, **asdict(self.state)}, separators=(",", ":"))
        with tmp.open("w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(self.path)
