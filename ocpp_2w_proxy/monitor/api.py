"""The monitor's read-only HTTP API (``docs/monitor_api.md``): routing, the browser guard and error mapping.

No query result is serialized on the loop: every database route awaits the reader thread, which runs the query
and returns the response body as ready JSON bytes.

There is no authentication. The guard refuses any request whose Origin is not the monitor itself, so a web
page the operator happens to visit cannot read the API from a script.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from ..config import Config
from ..minihttp import JSON_HEADERS, Content, EventStream, HttpError, Reply, Request
from .compact_json import dumps_bytes
from .exchange_filters import SQLITE_INTEGER_RANGE, parse_exchange_query
from .live_hub import LiveHub, TooManySubscribers
from .queries import InvalidCursor, ReadTimeLimit, exchange_detail, links_report, list_exchanges, session_summary
from .sqlite_worker import SqliteWorker

type Clock = Callable[[], float]
# The live state of the channels, taken on the loop; see links_report and docs/monitor_api.md.
type LiveSnapshot = Callable[[], Mapping[str, Any]]
type ConnectedChargers = Callable[[], Collection[str]]
Route = Callable[["MonitorApi", Request, re.Match[str]], Awaitable[Reply]]

LINK_HISTORY_SECONDS = 24 * 3600

logger = logging.getLogger(__name__)


class MonitorApi:
    def __init__(
        self,
        config: Config,
        reader: SqliteWorker,
        hub: LiveHub,
        live_snapshot: LiveSnapshot,
        connected_chargers: ConnectedChargers,
        clock: Clock = time.time,
    ):
        self._charger_ids = tuple(config.chargers)
        self._config_body = dumps_bytes(_config_view(config))
        self._reader = reader
        self._hub = hub
        self._live_snapshot = live_snapshot
        self._connected_chargers = connected_chargers
        self._clock = clock

    async def handle(self, request: Request) -> Reply:
        if request.method != "GET":
            raise HttpError(405, "method_not_allowed", "the monitor API is read-only: use GET")
        if not _same_origin(request.headers):
            raise HttpError(403, "origin_forbidden", "cross-origin requests are refused")
        for pattern, handler in ROUTES:
            if match := pattern.fullmatch(request.path):
                try:
                    return await handler(self, request, match)
                except ReadTimeLimit:
                    # Parameter names only: a search value can be an RFID id_tag.
                    logger.warning("monitor read %s %s hit the time limit", request.path, sorted(request.query))
                    raise HttpError(503, "query_too_slow", "the query took too long; narrow the filters") from None
        raise HttpError(404, "not_found", f"no such endpoint: {request.path}")

    async def _chargers(self, request: Request, match: re.Match[str]) -> Content:
        connected = self._connected_chargers()
        entries = [{"id": charger_id, "connected": charger_id in connected} for charger_id in self._charger_ids]
        return _json(dumps_bytes({"chargers": entries}))

    async def _config(self, request: Request, match: re.Match[str]) -> Content:
        return _json(self._config_body)

    async def _exchanges(self, request: Request, match: re.Match[str]) -> Content:
        query = parse_exchange_query(request.query)
        try:
            return _json(await self._reader.run(partial(list_exchanges, now=self._clock()), query))
        except InvalidCursor:
            raise HttpError(400, "bad_request", "before is not a cursor from next") from None

    async def _exchange(self, request: Request, match: re.Match[str]) -> Content:
        exchange_id = _stored_integer(match["exchange_id"], "exchange")
        body = await self._reader.run(partial(exchange_detail, now=self._clock()), exchange_id)
        return _json(_found(body, "exchange"))

    async def _session(self, request: Request, match: re.Match[str]) -> Content:
        transaction_id = _stored_integer(match["tx"], "session")
        return _json(_found(await self._reader.run(session_summary, transaction_id), "session"))

    async def _links(self, request: Request, match: re.Match[str]) -> Content:
        snapshot = self._live_snapshot()
        since = self._clock() - LINK_HISTORY_SECONDS
        return _json(await self._reader.run(links_report, snapshot, since))

    async def _events(self, request: Request, match: re.Match[str]) -> EventStream:
        try:
            return EventStream(self._hub.subscribe())
        except TooManySubscribers as exc:
            raise HttpError(503, "too_many_subscribers", str(exc)) from None


# Dispatch table: path pattern → handler. Every route is a GET.
ROUTES: tuple[tuple[re.Pattern[str], Route], ...] = (
    (re.compile(r"/api/chargers"), MonitorApi._chargers),
    (re.compile(r"/api/config"), MonitorApi._config),
    (re.compile(r"/api/exchanges"), MonitorApi._exchanges),
    (re.compile(r"/api/exchanges/(?P<exchange_id>[0-9]{1,19})"), MonitorApi._exchange),
    (re.compile(r"/api/sessions/(?P<tx>-?[0-9]{1,19})"), MonitorApi._session),
    (re.compile(r"/api/links"), MonitorApi._links),
    (re.compile(r"/api/events"), MonitorApi._events),
)


def _same_origin(headers: Mapping[str, str]) -> bool:
    """True without an Origin (not a cross-site script), or when the Origin names the Host it was sent to."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = headers.get("host", "")
    try:
        origin_host = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(host) and origin_host.lower() == host.lower()


def _config_view(config: Config) -> dict[str, Any]:
    return {
        "primary": config.primary.name,
        "secondaries": [
            {"name": secondary.name, "forward_actions": sorted(secondary.forward_actions)}
            for secondary in config.secondaries
        ],
        "chargers": list(config.chargers),
        "retention_days": config.monitor.retention_days,
    }


def _stored_integer(text: str, kind: str) -> int:
    """The id from the path; one too large for SQLite cannot name anything stored."""
    number = int(text)
    if number not in SQLITE_INTEGER_RANGE:
        raise HttpError(404, "not_found", f"no such {kind}: {text}")
    return number


def _found(body: bytes | None, kind: str) -> bytes:
    if body is None:
        raise HttpError(404, "not_found", f"no such {kind}")
    return body


def _json(body: bytes) -> Content:
    return Content(200, body, "application/json", JSON_HEADERS)
