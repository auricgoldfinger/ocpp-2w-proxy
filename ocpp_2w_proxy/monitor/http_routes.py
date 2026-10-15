"""The monitor listener's one handler: ``/api`` and everything under it to the API, the rest to the static UI."""

from __future__ import annotations

from ..minihttp import Handler, Reply, Request

API_PREFIX = "/api"


class MonitorRouter:
    def __init__(self, api: Handler, static: Handler):
        self._api = api
        self._static = static

    async def handle(self, request: Request) -> Reply:
        if request.path == API_PREFIX or request.path.startswith(f"{API_PREFIX}/"):
            return await self._api(request)
        return await self._static(request)
