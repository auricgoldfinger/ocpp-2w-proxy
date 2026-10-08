"""Reconnect and retry delays: exponential backoff with ±50% jitter (NFR-003).

Retries continue indefinitely; delays start at 1 second, double up to 300 seconds and
are randomized so that many proxies do not reconnect in lockstep.
"""

from __future__ import annotations

import random

MIN_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 300.0


class Backoff:
    """Schedule of retry delays; reset() starts over after a successful step."""

    def __init__(self, minimum: float | None = None, maximum: float | None = None):
        self._minimum = minimum if minimum is not None else MIN_RETRY_DELAY
        self._maximum = maximum if maximum is not None else MAX_RETRY_DELAY
        self._delay = self._minimum

    @property
    def current(self) -> float:
        """The un-randomized delay the next attempt will roughly wait."""
        return self._delay

    def next_delay(self) -> float:
        """Return the next randomized delay and advance the schedule."""
        delay = self._delay * random.uniform(0.5, 1.5)
        self._delay = min(self._delay * 2, self._maximum)
        return delay

    def reset(self) -> None:
        self._delay = self._minimum
