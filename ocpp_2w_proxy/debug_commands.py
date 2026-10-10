"""Debug commands: validate a request, send it to the connected charger and describe the outcome.

HTTP-agnostic: failures are raised as DebugError carrying the status the API should answer with.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from .debug_config import DebugConfig
from .debug_target import ChargerGone, CommandTimeout
from .ocpp import Call, CallError, CallResult, Reply, serialize
from .policy import CHARGER_BOUND_ACTIONS

logger = logging.getLogger(__name__)


class DebugError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


class Outcome(Protocol):
    sent: Call
    reply: Reply
    latency_ms: int


class CommandSession(Protocol):
    connected_since: str
    remote: str | None

    async def send_command(self, action: str, payload: dict, timeout: float) -> Outcome: ...


class SessionDirectory(Protocol):
    def session_for(self, charger_id: str) -> CommandSession | None: ...

    def charger_ids(self) -> tuple[str, ...]: ...


def _call_result(reply: CallResult) -> dict[str, Any]:
    return {"status": "CallResult", "result": reply.payload}


def _call_error(reply: CallError) -> dict[str, Any]:
    return {
        "status": "CallError",
        "error": {"code": reply.code, "description": reply.description, "details": reply.details},
    }


_DESCRIBE_REPLY: dict[type, Callable[[Any], dict[str, Any]]] = {CallResult: _call_result, CallError: _call_error}


class DebugCommands:
    def __init__(self, directory: SessionDirectory, config: DebugConfig):
        self._directory = directory
        self._config = config
        self._busy: set[str] = set()

    def chargers(self) -> dict[str, Any]:
        entries = []
        for charger_id in self._directory.charger_ids():
            session = self._directory.session_for(charger_id)
            entries.append(
                {
                    "id": charger_id,
                    "connected": session is not None,
                    "connected_since": session.connected_since if session else None,
                    "remote": session.remote if session else None,
                }
            )
        return {"chargers": entries}

    async def send(self, charger_id: str, body: Any) -> dict[str, Any]:
        action, payload, timeout = self._validate(body)
        if charger_id not in self._directory.charger_ids():
            raise DebugError(404, "unknown_charger", f"{charger_id!r} is not a configured charger")
        session = self._directory.session_for(charger_id)
        if session is None:
            raise DebugError(404, "not_connected", f"{charger_id!r} is not connected")
        # No await between the check and the add: the per-charger lock cannot be taken twice.
        if charger_id in self._busy:
            raise DebugError(409, "busy", f"a debug command to {charger_id!r} is still in flight")
        self._busy.add(charger_id)
        logger.info("debug command %s to %s (timeout %ss)", action, charger_id, timeout)
        try:
            outcome = await self._send(session, action, payload, timeout)
        except DebugError as exc:
            logger.info("debug command %s to %s failed: %s", action, charger_id, exc.code)
            raise
        finally:
            self._busy.discard(charger_id)
        logger.info("debug command %s to %s answered in %d ms", action, charger_id, outcome.latency_ms)
        return self._describe(action, outcome)

    def _validate(self, body: Any) -> tuple[str, dict, float]:
        if not isinstance(body, dict):
            raise DebugError(400, "invalid_request", "the body must be a JSON object")
        action, payload, timeout = body.get("action"), body.get("payload", {}), body.get("timeout")
        if action not in CHARGER_BOUND_ACTIONS:  # also catches a missing or non-string action
            raise DebugError(
                400, "invalid_action", f"action must be one of the OCPP 1.6 actions sent to a charger, not {action!r}"
            )
        if not isinstance(payload, dict):
            raise DebugError(400, "invalid_request", "payload must be a JSON object")
        if timeout is None:
            timeout = self._config.default_timeout
        elif isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
            raise DebugError(400, "invalid_request", "timeout must be a positive number of seconds")
        return action, payload, min(timeout, self._config.max_timeout)

    @staticmethod
    async def _send(session: CommandSession, action: str, payload: dict, timeout: float) -> Outcome:
        try:
            return await session.send_command(action, payload, timeout)
        except ChargerGone:
            raise DebugError(502, "charger_disconnected", "the charger disconnected before it answered") from None
        except CommandTimeout as exc:
            raise DebugError(504, "charger_timeout", f"no answer within {timeout}s", sent=_frame(exc.sent)) from None

    @staticmethod
    def _describe(action: str, outcome: Outcome) -> dict[str, Any]:
        return {
            **_DESCRIBE_REPLY[type(outcome.reply)](outcome.reply),
            "answered_by": "charger",
            "message_id": outcome.sent.id,
            "action": action,
            "latency_ms": outcome.latency_ms,
            "sent": _frame(outcome.sent),
            "received": _frame(outcome.reply),
        }


def _frame(message: Call | Reply) -> list[Any]:
    """The OCPP-J frame as JSON-ready data, exactly as it travelled."""
    return json.loads(serialize(message))
