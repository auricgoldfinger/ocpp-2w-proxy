"""Crash-safe persistence of per-charger proxy state (one JSON file per charger)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .ocpp import Call, from_dict

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
# How long save_soon() waits for more changes before writing once (crash window).
FLUSH_DELAY = 0.25


def restore_outbox(items: Sequence[dict[str, Any]]) -> list[tuple[Call, str | None]]:
    """Restore persisted queued calls as (call, start_ref), skipping unreadable entries.

    One malformed entry must not keep the whole queue - billing data - from being
    restored; that entry alone is dropped, loudly.
    """
    restored: list[tuple[Call, str | None]] = []
    for item in items:
        try:
            call = from_dict(item.get("call", item))
        except AttributeError, KeyError, TypeError:
            logger.error("skipping unreadable queued entry %r in the state file", item)
            continue
        restored.append((call, item.get("start_ref")))
    return restored


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
        self._dirty = False
        self._flusher: asyncio.Task | None = None

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
        """Write the state out now: for rare, transaction-critical changes."""
        self._dirty = False
        self._write()

    def save_soon(self) -> None:
        """Schedule a write shortly: bursts of changes (one per backend, per message)
        coalesce into one write instead of one fsync each. Requires a running loop."""
        self._dirty = True
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush_later())

    def flush(self) -> None:
        """Write out anything still pending; the channels call this when they close."""
        flusher = self._flusher
        self._flusher = None
        if flusher is not None:
            flusher.cancel()  # it would find nothing left to write
        if self._dirty:
            self._dirty = False
            self._write()

    async def _flush_later(self) -> None:
        await asyncio.sleep(FLUSH_DELAY)
        if self._dirty:
            self._dirty = False
            self._write()

    def _write(self) -> None:
        payload = json.dumps({"version": SCHEMA_VERSION, **asdict(self.state)}, separators=(",", ":"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)
        except OSError as exc:
            # A failing disk (full, read-only volume) must not take the message relay
            # down with it: the state stays in memory and retries on the next save.
            logger.error("cannot persist state to %s (%s); continuing in memory", self.path, exc)
