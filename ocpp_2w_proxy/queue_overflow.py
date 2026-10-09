"""Which queued item to give up when a durable queue is over its limit.

Meter data is expendable, stops and starts are not: the first meter reading goes first,
the oldest item otherwise. Callers leave the item currently being sent out of the
candidates: removing it would make the sender pop the next item, which was never sent,
losing it silently.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

EXPENDABLE_ACTION = "MeterValues"


def pick_victim[T](candidates: Sequence[T], action_of: Callable[[T], str]) -> T | None:
    """The candidate to drop, or None when there is none."""
    if not candidates:
        return None
    return next((item for item in candidates if action_of(item) == EXPENDABLE_ACTION), candidates[0])
