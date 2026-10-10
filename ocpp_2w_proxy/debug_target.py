"""A command issued by the debug endpoint: its answer comes back through a Future."""

from __future__ import annotations

import asyncio

from .ocpp import Call, Reply


class ChargerGone(Exception):
    """The charger's connection closed before it answered."""


class CommandTimeout(Exception):
    """The charger did not answer in time. `sent` is the Call that went out on the wire."""

    def __init__(self, sent: Call):
        super().__init__(f"no answer to {sent.action}")
        self.sent = sent


class FutureReplyTarget:
    """A ReplyTarget (see command_router) that hands the charger's reply to a waiting caller."""

    name = "debug"

    def __init__(self) -> None:
        self._future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()

    async def reply(self, message: Reply) -> None:
        if not self._future.done():
            self._future.set_result(message)

    def fail(self, error: Exception) -> None:
        if not self._future.done():
            self._future.set_exception(error)

    async def wait(self, timeout: float) -> Reply:
        """Raises TimeoutError, or whatever fail() was given."""
        return await asyncio.wait_for(self._future, timeout)
