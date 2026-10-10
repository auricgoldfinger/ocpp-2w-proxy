"""The debug endpoint's HTTP API: routing, the browser guard and error mapping.

  GET  /debug/chargers                  configured chargers and whether each is connected
  POST /debug/chargers/{id}/commands    send {"action", "payload"?, "timeout"?} to the charger

There is no authentication: the guard keeps browsers (and so a web page the operator happens
to visit) from driving it, by refusing any request that carries an Origin header and any
POST that is not application/json, which a cross-site form or simple request cannot send.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .debug_commands import DebugCommands, DebugError
from .minihttp import HttpError, Request, Response

Route = Callable[["DebugApi", Request, re.Match[str]], Awaitable[Any]]


class DebugApi:
    def __init__(self, commands: DebugCommands):
        self._commands = commands

    async def handle(self, request: Request) -> Response:
        if "origin" in request.headers:
            return _error(403, "origin_forbidden", "browser requests are refused: this endpoint has no authentication")
        try:
            handler, match = self._route(request)
            return Response(200, await handler(self, request, match))
        except DebugError as exc:
            return _error(exc.status, exc.code, exc.message, **exc.extra)
        except HttpError as exc:
            return _error(exc.status, exc.code, exc.message)

    def _route(self, request: Request) -> tuple[Route, re.Match[str]]:
        allowed = []
        for method, pattern, handler in ROUTES:
            if match := pattern.fullmatch(request.path):
                if method == request.method:
                    return handler, match
                allowed.append(method)
        if allowed:
            raise HttpError(405, "method_not_allowed", f"use {' or '.join(allowed)} for {request.path}")
        raise HttpError(404, "not_found", f"no such endpoint: {request.path}")

    async def _list_chargers(self, request: Request, match: re.Match[str]) -> dict[str, Any]:
        return self._commands.chargers()

    async def _send_command(self, request: Request, match: re.Match[str]) -> dict[str, Any]:
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise HttpError(415, "unsupported_media_type", "Content-Type must be application/json")
        try:
            body = json.loads(request.body)
        except ValueError:
            raise HttpError(400, "invalid_json", "the body is not valid JSON") from None
        return await self._commands.send(match["charger_id"], body)


# Dispatch table: (method, path pattern, handler).
ROUTES: tuple[tuple[str, re.Pattern[str], Route], ...] = (
    ("GET", re.compile(r"/debug/chargers"), DebugApi._list_chargers),
    ("POST", re.compile(r"/debug/chargers/(?P<charger_id>[^/]+)/commands"), DebugApi._send_command),
)


def _error(status: int, code: str, message: str, **extra: Any) -> Response:
    return Response(status, {"error": code, "message": message, **extra})
