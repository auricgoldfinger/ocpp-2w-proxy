"""How long monitor code may hold the event loop before it gives the relay a turn.

A slice ends when its time budget is spent or after ``max_items`` items, whichever comes first. The budget, not
the count, is what keeps the relay's tail latency low: every loop iteration the writer is busy adds one slice to
whatever relay callback is waiting, and a relay round trip spans several iterations.
"""

from __future__ import annotations

import asyncio
import time

SLICE_BUDGET = 0.0001  # seconds of loop time per slice
SLICE_MAX_ITEMS = 50

_clock = time.perf_counter


class LoopSlice:
    """Counts work done since the last yield; ``spent()`` says when to yield, ``yield_to_loop()`` does it."""

    __slots__ = ("_budget", "_max_items", "_deadline", "_items")

    def __init__(self, budget: float = SLICE_BUDGET, max_items: int = SLICE_MAX_ITEMS):
        self._budget = budget
        self._max_items = max_items
        self.restart()

    def spent(self) -> bool:
        """Count one item; True when this slice has used its budget or its item cap."""
        self._items += 1
        return self._items >= self._max_items or _clock() >= self._deadline

    async def yield_to_loop(self) -> None:
        await asyncio.sleep(0)
        self.restart()

    def restart(self) -> None:
        """Start a new slice; call it whenever the caller resumes from an await of its own."""
        self._items = 0
        self._deadline = _clock() + self._budget
