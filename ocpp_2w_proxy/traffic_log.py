"""Uniform, redacted logging of OCPP frames flowing through the proxy."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .ocpp import Message
from .redact import describe

logger = logging.getLogger("ocpp_2w_proxy.traffic")

PROXY = "proxy"
CHARGER = "charger"


class TrafficLog:
    """Logs one line per frame as ``<source> -> <target>``, aligned across all peers.

    Payloads (redacted) only at DEBUG and only when enabled.
    """

    def __init__(self, charger_id: str, include_payloads: bool, peers: Iterable[str] = ()):
        self._charger_id = charger_id
        self._include_payloads = include_payloads
        self._width = max(len(name) for name in (PROXY, CHARGER, *peers))

    def received(self, peer: str, message: Message) -> None:
        self._frame(peer, PROXY, message)

    def sent(self, peer: str, message: Message) -> None:
        self._frame(PROXY, peer, message)

    def _frame(self, source: str, target: str, message: Message) -> None:
        direction = f"{source:>{self._width}} -> {target:<{self._width}}"
        if logger.isEnabledFor(logging.DEBUG) and self._include_payloads:
            logger.debug("%s %s %s", self._charger_id, direction, describe(message, include_payload=True))
        else:
            logger.info("%s %s %s", self._charger_id, direction, describe(message, include_payload=False))
