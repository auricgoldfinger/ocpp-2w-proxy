"""The writer's in-memory projections: exchanges that may still receive events, evicted once they go quiet.

A settled exchange (every leg ended) is kept only for a short grace period, long enough for the proxy's own reply
to its origin. An open one is kept until it has been idle well past the stale limit. An event for an evicted
exchange is not lost: the writer rebuilds its projection from the store.

Each kind lives in its own touch-ordered table with one fixed idle time, so the head is always the next to
expire and eviction never scans.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from itertools import chain
from typing import Any

from .gaps import ExchangeSpan
from .projection import DEFAULT_STALE_AFTER, ExchangeProjection

SETTLED_GRACE = 10.0
OPEN_IDLE = 2 * DEFAULT_STALE_AFTER
MAX_EXCHANGES = 20_000


@dataclass
class TrackedExchange:
    projection: ExchangeProjection
    # The origin payload as relayed, before redaction: the fast ``is`` check for unchanged legs. None after a rebuild
    # from the store, which only holds the redacted origin.
    raw_origin: Mapping[str, Any] | None = None
    # True until the exchange's first event has been folded; a rebuilt exchange already had its first event.
    fresh: bool = True
    touched_at: float = 0.0

    @property
    def exchange_id(self) -> int:
        return self.projection.exchange_id

    def span(self) -> ExchangeSpan:
        projection = self.projection
        return ExchangeSpan(
            projection.exchange_id,
            projection.charger_id or "",
            projection.started_at or 0.0,
            projection.last_at or 0.0,
            open=not projection.settled,
            orphan=projection.origin is None,
        )


class OpenExchanges:
    def __init__(
        self,
        *,
        settled_grace: float = SETTLED_GRACE,
        open_idle: float = OPEN_IDLE,
        max_exchanges: int = MAX_EXCHANGES,
    ):
        self._idle = {True: settled_grace, False: open_idle}
        self._max_exchanges = max_exchanges
        self._tables: dict[bool, OrderedDict[int, TrackedExchange]] = {True: OrderedDict(), False: OrderedDict()}

    def __len__(self) -> int:
        return len(self._tables[True]) + len(self._tables[False])  # on every touch: no generator

    def __contains__(self, exchange_id: int) -> bool:
        return any(exchange_id in table for table in self._tables.values())

    def __iter__(self) -> Iterator[TrackedExchange]:
        return chain.from_iterable(table.values() for table in self._tables.values())

    def get(self, exchange_id: int) -> TrackedExchange | None:
        return self._tables[True].get(exchange_id) or self._tables[False].get(exchange_id)

    def touch(self, tracked: TrackedExchange, now: float) -> None:
        """Track the exchange, or restart its idle time after it changed; settling moves it to the short table."""
        exchange_id = tracked.exchange_id
        for table in self._tables.values():
            table.pop(exchange_id, None)
        tracked.touched_at = now
        self._tables[tracked.projection.settled][exchange_id] = tracked
        while len(self) > self._max_exchanges:
            self._oldest_table().popitem(last=False)

    def evict(self, now: float) -> None:
        for settled, table in self._tables.items():
            cutoff = now - self._idle[settled]
            while table and next(iter(table.values())).touched_at <= cutoff:
                table.popitem(last=False)

    def _oldest_table(self) -> OrderedDict[int, TrackedExchange]:
        settled, open_ = self._tables[True], self._tables[False]
        if not settled or not open_:
            return settled or open_
        return settled if next(iter(settled.values())).touched_at <= next(iter(open_.values())).touched_at else open_
