"""The Flow Monitor's lifecycle: the store, the writer, the purge and the HTTP listener, started and closed in order.

The recorders exist from construction on, because the proxy wires them into each charger's flow before it
starts. Everything else exists only once ``start`` has opened the store. The monitor never stops the proxy: if
its store cannot be opened or its port cannot be bound, it logs an error, stops recording and stays off.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .. import minihttp
from ..config import Config
from ..listen_address import warn_unless_loopback
from . import db
from .api import MonitorApi
from .http_routes import MonitorRouter
from .live_hub import LiveHub
from .live_snapshot import LiveSnapshot, ProxyState
from .not_sent import NotSentMap
from .recorder import MonitorRecorder
from .recorder_hub import RecorderHub
from .retention import RetentionPurge
from .sqlite_worker import SqliteWorker
from .static_files import StaticFiles
from .writer import MonitorWriter

logger = logging.getLogger(__name__)

MAX_CONNECTIONS = 16
WRITER_CLOSE_TIMEOUT = 2.0


@dataclass(frozen=True)
class _Running:
    store: SqliteWorker
    reader: SqliteWorker
    hub: LiveHub
    writer: MonitorWriter
    purge: RetentionPurge
    server: asyncio.Server


class MonitorService:
    def __init__(self, config: Config, proxy: ProxyState):
        self._config = config
        self._proxy = proxy
        self._recorders = RecorderHub(config.chargers)
        self._running: _Running | None = None

    def recorder_for(self, charger_id: str) -> MonitorRecorder:
        return self._recorders.recorder_for(charger_id)

    @property
    def port(self) -> int | None:
        """The port the monitor listens on; None when it is not running."""
        return self._running.server.sockets[0].getsockname()[1] if self._running else None

    async def start(self) -> None:
        settings = self._config.monitor
        path = settings.db_path
        assert path is not None, "parse_monitor resolves db_path"
        store = SqliteWorker(path, readonly=False)
        reader = SqliteWorker(path, readonly=True)
        try:
            await _open(store, reader, path)
        except (sqlite3.Error, OSError) as exc:
            await _give_up(store, reader)
            self._stay_off(f"its store {path} cannot be opened: {exc}")
            return
        hub = LiveHub()
        writer = MonitorWriter(self._recorders, store, hub, NotSentMap.from_config(self._config))
        purge = RetentionPurge(store, settings.retention_days)
        snapshot = LiveSnapshot(self._proxy, self._config.primary.name, self._recorders, writer)
        api = MonitorApi(self._config, reader, hub, snapshot, snapshot.connected_chargers)
        router = MonitorRouter(api.handle, StaticFiles().handle)
        try:
            server = await minihttp.serve(router.handle, settings.listen, settings.port, MAX_CONNECTIONS)
        except OSError as exc:
            await _give_up(store, reader)
            self._stay_off(f"it cannot listen on {settings.listen}:{settings.port}: {exc}")
            return
        writer.start()
        purge.start()
        self._running = _Running(store, reader, hub, writer, purge, server)
        logger.info("flow monitor on http://%s:%d/ (store %s)", settings.listen, self.port, path)
        warn_unless_loopback(
            logger, settings.listen, "flow monitor", "anyone who can connect can read the (masked) message flow"
        )

    async def close(self) -> None:
        running, self._running = self._running, None
        if running is None:
            return
        running.hub.close()
        await running.purge.close()
        try:
            await asyncio.wait_for(running.writer.close(), WRITER_CLOSE_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "flow monitor writer did not finish within %ss; its last events are lost", WRITER_CLOSE_TIMEOUT
            )
        running.server.close()
        running.server.close_clients()
        await running.server.wait_closed()
        await running.store.close()
        await running.reader.close()

    def _stay_off(self, why: str) -> None:
        self._recorders.detach()
        logger.error("flow monitor disabled because %s; the proxy runs without it", why)


async def _open(store: SqliteWorker, reader: SqliteWorker, path: Path) -> None:
    """The schema up to date through the writing connection, then the read-only one opened beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    await store.run(db.prepare_writer)
    await reader.run(_schema_version)


def _schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


async def _give_up(*workers: SqliteWorker) -> None:
    for worker in workers:
        with suppress(sqlite3.Error):  # a connection that never opened properly has nothing left to release
            await worker.close()
