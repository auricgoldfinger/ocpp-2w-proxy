"""Rate limiting for a log line that a persistent fault would otherwise repeat on every attempt."""

from __future__ import annotations

import time
from collections.abc import Callable

DEFAULT_INTERVAL = 60.0


class LogThrottle:
    """``ready()`` is True at most once per ``interval``; the first call always is."""

    def __init__(self, interval: float = DEFAULT_INTERVAL, clock: Callable[[], float] = time.monotonic):
        self._interval = interval
        self._clock = clock
        self._last: float | None = None

    def ready(self) -> bool:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return False
        self._last = now
        return True
