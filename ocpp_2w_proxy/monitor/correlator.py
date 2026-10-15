"""Matches replies to the exchange whose Call they answer (pure; runs in the writer, never on the relay).

Every Call sent to a peer is registered under ``(charger, peer, wire_id)``. A reply pops its entry into a
bounded tombstone ring, so a second reply still finds its exchange and is flagged *duplicate*. A timed-out
entry stays in the map, so a late reply still attaches and is flagged *late*.

All work is O(1) per call (amortised): expiry runs from the head in insertion order, the map is capped, and
clearing a peer on link down walks only that peer's own entries.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import NamedTuple

EXPIRY_SECONDS = 3600.0
MAX_ENTRIES = 10_000
MAX_TOMBSTONES = 2_000

type _Key = tuple[str, str, str]
type _Link = tuple[str, str]


class ReplyFlags(NamedTuple):
    late: bool
    duplicate: bool


class _Pending(NamedTuple):
    exchange_id: int
    at: float
    timed_out: bool


class _Tombstone(NamedTuple):
    exchange_id: int
    late: bool
    link_epoch: int


class Correlator:
    def __init__(
        self,
        *,
        expiry: float = EXPIRY_SECONDS,
        max_entries: int = MAX_ENTRIES,
        max_tombstones: int = MAX_TOMBSTONES,
    ):
        self._expiry = expiry
        self._max_entries = max_entries
        self._max_tombstones = max_tombstones
        self._pending: OrderedDict[_Key, _Pending] = OrderedDict()
        self._by_link: dict[_Link, set[str]] = {}
        self._tombstones: OrderedDict[_Key, _Tombstone] = OrderedDict()
        # Bumped on link down; tombstones from an older epoch no longer match (wire ids get reused).
        self._link_epochs: dict[_Link, int] = {}

    def __len__(self) -> int:
        return len(self._pending)

    def register_sent(self, charger: str, peer: str, wire_id: str, exchange_id: int, at: float) -> None:
        key = (charger, peer, wire_id)
        self._remove(key)
        self._pending[key] = _Pending(exchange_id, at, False)
        self._by_link.setdefault((charger, peer), set()).add(wire_id)
        self._expire(at)
        while len(self._pending) > self._max_entries:
            self._remove(next(iter(self._pending)))

    def resolve_reply(self, charger: str, peer: str, wire_id: str, at: float) -> tuple[int, ReplyFlags] | None:
        """The exchange this reply belongs to, or None for an orphan."""
        self._expire(at)
        key = (charger, peer, wire_id)
        pending = self._remove(key)
        if pending is not None:
            self._bury(key, _Tombstone(pending.exchange_id, pending.timed_out, self._epoch((charger, peer))))
            return pending.exchange_id, ReplyFlags(late=pending.timed_out, duplicate=False)
        tombstone = self._tombstones.get(key)
        if tombstone is not None and tombstone.link_epoch == self._epoch((charger, peer)):
            return tombstone.exchange_id, ReplyFlags(late=tombstone.late, duplicate=True)
        return None

    def mark_timed_out(self, charger: str, peer: str, wire_id: str) -> None:
        """The proxy gave up waiting; the entry stays so a late reply still attaches."""
        key = (charger, peer, wire_id)
        pending = self._pending.get(key)
        if pending is not None:
            self._pending[key] = pending._replace(timed_out=True)

    def link_down(self, charger: str, peer: str) -> None:
        """OCPP-J replies never cross connections, and peers reuse message ids after a reconnect."""
        link = (charger, peer)
        for wire_id in self._by_link.pop(link, ()):
            self._pending.pop((charger, peer, wire_id), None)
        self._link_epochs[link] = self._epoch(link) + 1

    def _epoch(self, link: _Link) -> int:
        return self._link_epochs.get(link, 0)

    def _expire(self, now: float) -> None:
        cutoff = now - self._expiry
        while self._pending:
            key, oldest = next(iter(self._pending.items()))
            if oldest.at >= cutoff:
                return
            self._remove(key)

    def _remove(self, key: _Key) -> _Pending | None:
        pending = self._pending.pop(key, None)
        if pending is not None:
            charger, peer, wire_id = key
            wire_ids = self._by_link.get((charger, peer))
            if wire_ids is not None:
                wire_ids.discard(wire_id)
                if not wire_ids:
                    del self._by_link[(charger, peer)]
        return pending

    def _bury(self, key: _Key, tombstone: _Tombstone) -> None:
        self._tombstones.pop(key, None)
        self._tombstones[key] = tombstone
        while len(self._tombstones) > self._max_tombstones:
            self._tombstones.popitem(last=False)
