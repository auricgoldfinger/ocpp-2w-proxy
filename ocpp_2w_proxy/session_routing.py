"""Where a backend channel sends the commands its backend issues: to the charger session
that is attached right now, if any.

A channel outlives charger sessions. Each attach() hands out a token; only the session
holding the latest token may detach, so a replaced session that outlives the server's
replacement timeout cannot unhook its successor during cleanup (UC-002 BR-004).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .backend_link import BackendLink
from .ocpp import Call, CallError

OnCall = Callable[[Call], Awaitable[None]]


class SessionRouting:
    def __init__(self) -> None:
        self._on_call: OnCall | None = None
        self._generation = 0

    @property
    def attached(self) -> bool:
        return self._on_call is not None

    def attach(self, on_call: OnCall) -> int:
        self._on_call = on_call
        self._generation += 1
        return self._generation

    def detach(self, token: int) -> bool:
        """True if routing ended; False when a newer session owns it now."""
        if token != self._generation:
            return False
        self._on_call = None
        return True

    async def dispatch(self, call: Call, link: BackendLink | None) -> None:
        """Hand a backend command to the attached session; without one, answer it on `link`."""
        if self._on_call is None:
            if link is not None:
                await link.reply(CallError(call.id, "GenericError", "charger is disconnected"))
            return
        await self._on_call(call)
