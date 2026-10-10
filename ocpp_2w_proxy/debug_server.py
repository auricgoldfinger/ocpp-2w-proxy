"""Lifecycle of the debug endpoint's listener (see debug_api for what it serves)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from . import minihttp
from .debug_api import DebugApi
from .debug_commands import DebugCommands, SessionDirectory
from .debug_config import DebugConfig

logger = logging.getLogger(__name__)


class DebugServer:
    def __init__(self, config: DebugConfig, directory: SessionDirectory):
        self._config = config
        self._api = DebugApi(DebugCommands(directory, config))
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None, "not started"
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await minihttp.serve(self._api.handle, self._config.listen, self._config.port)
        logger.info("debug endpoint listening on http://%s:%d/debug", self._config.listen, self.port)
        if not _is_loopback(self._config.listen):
            logger.warning(
                "debug endpoint is reachable beyond this host and has no authentication: "
                "anyone who can connect can send commands to your chargers"
            )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
