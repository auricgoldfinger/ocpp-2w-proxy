"""Uniform, redacted logging of OCPP frames flowing through the proxy."""

from __future__ import annotations

import logging

from .ocpp import Message
from .redact import describe

logger = logging.getLogger("ocpp_2w_proxy.traffic")


class TrafficLog:
    """Logs one line per frame. Payloads (redacted) only at DEBUG and only when enabled."""

    def __init__(self, charger_id: str, include_payloads: bool):
        self._charger_id = charger_id
        self._include_payloads = include_payloads

    def frame(self, direction: str, message: Message) -> None:
        if logger.isEnabledFor(logging.DEBUG) and self._include_payloads:
            logger.debug("%s %s %s", self._charger_id, direction, describe(message, include_payload=True))
        else:
            logger.info("%s %s %s", self._charger_id, direction, describe(message, include_payload=False))
