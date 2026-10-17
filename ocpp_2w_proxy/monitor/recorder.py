"""The monitor's end of the relay path: each hook enqueues one plain tuple and returns.

A hook does one ``time.time()``, builds one tuple in ``FlowEvent`` field order and calls ``put_nowait`` on the
queue every charger shares (see ``recorder_hub``). When the queue is full the event is dropped and counted per
charger; the relay never waits for the monitor. Everything else (correlation, redaction, storage) happens later,
in the writer.
"""

from __future__ import annotations

import asyncio
import time

from ..ocpp import Call, Message
from ..reasons import Reason
from .events import EventKind

_RECEIVED = EventKind.RECEIVED
_SENT = EventKind.SENT
_DECIDED = EventKind.DECIDED
_LINK = EventKind.LINK
_now = time.time


class MonitorRecorder:
    """The FlowRecorder of one charger. ``dropped`` and ``last_drop_at`` count the events the queue refused."""

    __slots__ = ("_charger_id", "_put", "dropped", "last_drop_at")

    def __init__(self, charger_id: str, queue: asyncio.Queue):
        self._charger_id = charger_id
        self._put = queue.put_nowait
        self.dropped = 0
        self.last_drop_at: float | None = None

    def received(self, peer: str, message: Message) -> None:
        self._record(_RECEIVED, peer, message, None, None)

    def sent(self, peer: str, message: Message) -> None:
        self._record(_SENT, peer, message, None, None)

    def decided(self, peer: str, call: Call, reason: Reason, detail: object = None) -> None:
        self._record(_DECIDED, peer, call, reason, detail)

    def link_changed(self, peer: str, up: bool, note: str | None = None) -> None:
        self._record(_LINK, peer, None, None, (up, note))

    def detach(self) -> None:
        """Record nothing from now on: the monitor could not start, so no writer will ever read the queue."""
        self._put = _discard

    def _record(self, kind: EventKind, peer: str, message: Message | None, reason: Reason | None, detail: object):
        at = _now()
        try:
            self._put((kind, at, self._charger_id, peer, message, reason, detail))
        except asyncio.QueueFull:
            self.dropped += 1
            self.last_drop_at = at


def _discard(event: tuple) -> None:
    pass
