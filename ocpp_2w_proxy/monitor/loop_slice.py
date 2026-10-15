"""How long monitor code may hold the event loop before it gives the relay a turn, and for how long it then waits.

A slice ends when its time budget is spent or after ``max_items`` items, whichever comes first. Yielding with
``sleep(0)`` alone is not enough: the writer would then take one slice in every loop iteration, and a relay round
trip spans about ten iterations, so each round trip would wait behind ten slices. After a slice the writer
therefore pauses long enough to keep its share of the loop at ``max_share``; a round trip then meets one slice at
most. When the loop has nothing else to do the pause costs some throughput, never latency.
"""

from __future__ import annotations

import asyncio
import time

SLICE_BUDGET = 0.0001  # seconds of loop time per slice
SLICE_MAX_ITEMS = 50
MAX_LOOP_SHARE = 0.25

_clock = time.perf_counter


class LoopSlice:
    """Counts work done since the last yield; ``spent()`` says when to yield, ``yield_to_loop()`` does it."""

    __slots__ = ("_budget", "_max_items", "_rest_factor", "_started", "_deadline", "_items")

    def __init__(
        self, budget: float = SLICE_BUDGET, max_items: int = SLICE_MAX_ITEMS, max_share: float = MAX_LOOP_SHARE
    ):
        self._budget = budget
        self._max_items = max_items
        self._rest_factor = (1 - max_share) / max_share
        self.restart()

    def spent(self) -> bool:
        """Count one item; True when this slice has used its budget or its item cap."""
        self._items += 1
        return self._items >= self._max_items or _clock() >= self._deadline

    async def yield_to_loop(self) -> None:
        """Rest in proportion to the slice just done, then start the next one."""
        await asyncio.sleep((_clock() - self._started) * self._rest_factor)
        self.restart()

    def restart(self) -> None:
        """Start a new slice; call it whenever the caller resumes from an await of its own."""
        self._items = 0
        self._started = _clock()
        self._deadline = self._started + self._budget
