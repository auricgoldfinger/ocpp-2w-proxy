"""Route the charger's replies back to whichever backend issued the command.

Backends choose their own message ids, so two backends may use the same id. Every command
gets a fresh proxy-unique id on its way to the charger; the reply is mapped back here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .ocpp import Call, Reply, new_message_id

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300


class ReplyTarget(Protocol):
    name: str

    async def reply(self, message: Reply) -> None: ...


@dataclass(frozen=True)
class _Route:
    target: ReplyTarget
    original_id: str
    action: str
    created: float


@dataclass(frozen=True)
class RoutedReply:
    target: ReplyTarget
    reply: Reply
    action: str


class CommandRouter:
    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._routes: dict[str, _Route] = {}

    def outbound(self, target: ReplyTarget, call: Call) -> Call:
        self._expire()
        proxy_id = new_message_id()
        self._routes[proxy_id] = _Route(target, call.id, call.action, self._clock())
        return call.with_id(proxy_id)

    def take(self, reply: Reply) -> RoutedReply | None:
        route = self._routes.pop(reply.id, None)
        if route is None:
            return None
        return RoutedReply(route.target, reply.with_id(route.original_id), route.action)

    def _expire(self) -> None:
        deadline = self._clock() - self._ttl
        for proxy_id in [pid for pid, route in self._routes.items() if route.created < deadline]:
            route = self._routes.pop(proxy_id)
            logger.warning("charger never answered %s from %s backend", route.action, route.target.name)
