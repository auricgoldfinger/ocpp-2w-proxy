"""Monitor gaps: turning growth of the recorder's per-charger drop counters into gap rows, and deciding which
exchanges the lost events may have belonged to (pure).

A gap only means the monitor lost events; the relay forwarded the traffic normally.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple


class Gap(NamedTuple):
    charger_id: str
    from_at: float
    to_at: float
    dropped: int


class ExchangeSpan(NamedTuple):
    """When an exchange the writer knows about was active, as far as a gap is concerned."""

    exchange_id: int
    charger_id: str
    started_at: float
    last_at: float
    open: bool
    orphan: bool


class GapDetector:
    """Remembers the drop counters it last saw; each observation reports the drops since the previous one."""

    def __init__(self, started_at: float):
        self._seen: dict[str, int] = {}
        self._observed_at = started_at

    def observe(self, dropped: Mapping[str, int], last_drop_at: Mapping[str, float], now: float) -> list[Gap]:
        """The window of a gap runs from the previous observation to the charger's last drop."""
        gaps = []
        for charger_id, count in dropped.items():
            new = count - self._seen.get(charger_id, 0)
            if new > 0:
                gaps.append(Gap(charger_id, self._observed_at, last_drop_at.get(charger_id, now), new))
            self._seen[charger_id] = count
        self._observed_at = now
        return gaps


def incomplete_exchanges(gap: Gap, spans: Iterable[ExchangeSpan], reply_window: float) -> list[int]:
    """The exchanges that were open during ``gap``, plus the orphans whose Call may have been lost in it.

    An orphan reply can arrive up to ``reply_window`` (a call timeout) after the gap that swallowed its Call.
    """
    return [
        span.exchange_id for span in spans if span.charger_id == gap.charger_id and _touches(gap, span, reply_window)
    ]


def _touches(gap: Gap, span: ExchangeSpan, reply_window: float) -> bool:
    if span.orphan:
        return gap.from_at <= span.started_at <= gap.to_at + reply_window
    return span.started_at <= gap.to_at and (span.open or span.last_at >= gap.from_at)
