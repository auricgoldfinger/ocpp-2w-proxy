"""The queue every charger's recorder feeds, and the per-charger drop counters the writer turns into gaps."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from contextlib import suppress

from .recorder import MonitorRecorder

QUEUE_SIZE_PER_CHARGER = 2000


class RecorderHub:
    """Owns the shared queue (2000 events per charger) and hands out one recorder per charger."""

    def __init__(self, charger_ids: Iterable[str], per_charger: int = QUEUE_SIZE_PER_CHARGER):
        ids = tuple(charger_ids)
        self.queue: asyncio.Queue = asyncio.Queue(per_charger * max(len(ids), 1))
        self._recorders = {charger_id: MonitorRecorder(charger_id, self.queue) for charger_id in ids}

    def recorder_for(self, charger_id: str) -> MonitorRecorder:
        return self._recorders[charger_id]

    def detach(self) -> None:
        """Every recorder stops recording and what is queued is let go; for a monitor that could not start."""
        for recorder in self._recorders.values():
            recorder.detach()
        with suppress(asyncio.QueueEmpty):
            while True:
                self.queue.get_nowait()

    def dropped(self) -> Mapping[str, int]:
        """Events dropped so far, per charger."""
        return {charger_id: recorder.dropped for charger_id, recorder in self._recorders.items()}

    def last_drop_at(self) -> Mapping[str, float]:
        """When each charger last lost an event; chargers that never did are absent."""
        return {
            charger_id: recorder.last_drop_at
            for charger_id, recorder in self._recorders.items()
            if recorder.last_drop_at is not None
        }
