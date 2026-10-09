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

from .message_classes import MessageClass, classify, latest_key
from .ocpp import Call

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3
# How long save_soon() waits for more changes before writing once (crash window).
FLUSH_DELAY = 0.25


def to_dict(call: Call) -> dict[str, Any]:
    """Representation used for persisting queued calls."""
    return {"id": call.id, "action": call.action, "payload": call.payload}


def from_dict(data: dict[str, Any]) -> Call:
    return Call(data["id"], data["action"], data["payload"])


def _call_of_entry(item: dict[str, Any]) -> Call | None:
    """The call stored in one persisted entry (current or pre-v3 layout); None if unreadable."""
    try:
        return from_dict(item.get("call", item))
    except AttributeError, KeyError, TypeError:
        return None


def restore_outbox(items: Sequence[dict[str, Any]]) -> list[tuple[Call, str | None]]:
    """Restore persisted queued calls as (call, start_ref), skipping unreadable entries.

    One malformed entry must not keep the whole queue - billing data - from being
    restored; that entry alone is dropped, loudly.
    """
    restored: list[tuple[Call, str | None]] = []
    for item in items:
        call = _call_of_entry(item)
        if call is None:
            logger.error("skipping unreadable queued entry %r in the state file", item)
            continue
        restored.append((call, item.get("start_ref")))
    return restored


def _migrate_v2(data: dict[str, Any]) -> None:
    """v2 queued every status and meter reading in the primary outbox. Keep the billing
    data there; state reports collapse to the newest per subject; stale readings go."""
    outbox: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    for item in data.get("primary_outbox", []):
        call = _call_of_entry(item)
        if call is None:
            outbox.append(item)  # left for restore_outbox to report and skip
            continue
        match classify(call):
            case MessageClass.DURABLE:
                outbox.append(item)
            case MessageClass.LATEST:
                latest[latest_key(call)] = to_dict(call)
    data["primary_outbox"] = outbox
    data["primary_latest"] = latest


@dataclass
class ChargerState:
    # primary transactionId (as str) -> {backend name: that backend's transactionId}
    transactions: dict[str, dict[str, int]] = field(default_factory=dict)
    # StartTransaction start_ref -> transactionId, while waiting for the backends' answers
    pending_primary_starts: dict[str, int] = field(default_factory=dict)
    # Backend name -> {StartTransaction start_ref: that backend's transactionId}
    pending_secondary_starts: dict[str, dict[str, int]] = field(default_factory=dict)
    # Primary calls acknowledged locally while the primary backend was unavailable
    primary_outbox: list[dict[str, Any]] = field(default_factory=list)
    # Newest state report (Status/Firmware/Diagnostics) per subject the primary has not
    # confirmed yet: latest_key -> call; sent after the outbox has drained.
    primary_latest: dict[str, dict[str, Any]] = field(default_factory=dict)
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
            version = data.pop("version", None)
            if version == 2:
                _migrate_v2(data)
            elif version != SCHEMA_VERSION:
                raise ValueError(f"unsupported state version {version!r}")
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
        self._write_if_dirty()

    async def _flush_later(self) -> None:
        await asyncio.sleep(FLUSH_DELAY)
        self._write_if_dirty()

    def _write_if_dirty(self) -> None:
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
