"""What the relay reports about each message, and fanning those reports out to several recorders.

The methods run inline on the relay path, so implementations must stay cheap: no await,
no I/O, no JSON, no copying of payloads.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .ocpp import Call, Message
from .reasons import Reason

logger = logging.getLogger(__name__)

_FAILURE_LOG_INTERVAL = 60.0


class FlowRecorder(Protocol):
    def received(self, peer: str, message: Message) -> None: ...

    def sent(self, peer: str, message: Message) -> None: ...

    def decided(self, peer: str, call: Call, reason: Reason, detail: object = None) -> None:
        """The proxy decided the fate of ``call``'s leg to ``peer``.

        ``detail`` is a value the call site already has (e.g. a transaction id); never build one just for this.
        """
        ...

    def link_changed(self, peer: str, up: bool) -> None: ...


class FanOut:
    """Reports to the log first, then to the monitor; a failing monitor never disturbs the relay."""

    def __init__(self, log: FlowRecorder, monitor: FlowRecorder):
        self._log = log
        self._monitor = monitor
        self.failures = 0
        self._last_failure_log = -_FAILURE_LOG_INTERVAL

    def received(self, peer: str, message: Message) -> None:
        self._log.received(peer, message)
        try:
            self._monitor.received(peer, message)
        except Exception:
            self._failed()

    def sent(self, peer: str, message: Message) -> None:
        self._log.sent(peer, message)
        try:
            self._monitor.sent(peer, message)
        except Exception:
            self._failed()

    def decided(self, peer: str, call: Call, reason: Reason, detail: object = None) -> None:
        self._log.decided(peer, call, reason, detail)
        try:
            self._monitor.decided(peer, call, reason, detail)
        except Exception:
            self._failed()

    def link_changed(self, peer: str, up: bool) -> None:
        self._log.link_changed(peer, up)
        try:
            self._monitor.link_changed(peer, up)
        except Exception:
            self._failed()

    def _failed(self) -> None:
        self.failures += 1
        now = time.monotonic()
        if now - self._last_failure_log >= _FAILURE_LOG_INTERVAL:
            self._last_failure_log = now
            logger.exception("monitor recorder failed (%d failures so far); relaying continues", self.failures)
