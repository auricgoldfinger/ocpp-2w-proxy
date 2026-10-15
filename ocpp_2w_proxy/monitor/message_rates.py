"""Messages per peer over the last hour, counted in one-minute buckets by the writer (never on the relay path)."""

from __future__ import annotations

from collections import deque

BUCKET_SECONDS = 60
WINDOW_BUCKETS = 60  # one hour

type _Key = tuple[str, str]


class MessageRates:
    """Frames exchanged per (charger, peer), kept as a rolling hour of per-minute counts.

    Each key holds at most ``WINDOW_BUCKETS`` buckets, so counting and reading cost the same at any traffic rate.
    """

    def __init__(self):
        self._buckets: dict[_Key, deque[list[int]]] = {}

    def count(self, charger_id: str, peer: str, at: float) -> None:
        minute = int(at // BUCKET_SECONDS)
        buckets = self._buckets.setdefault((charger_id, peer), deque())
        if buckets and buckets[-1][0] >= minute:
            buckets[-1][1] += 1  # same minute, or a clock step back: never reorder the buckets
            return
        buckets.append([minute, 1])
        while buckets[0][0] <= minute - WINDOW_BUCKETS:
            buckets.popleft()

    def last_hour(self, charger_id: str, peer: str, now: float) -> int:
        oldest = int(now // BUCKET_SECONDS) - WINDOW_BUCKETS
        buckets = self._buckets.get((charger_id, peer), ())
        return sum(count for minute, count in buckets if minute > oldest)
