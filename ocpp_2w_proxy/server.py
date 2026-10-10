"""WebSocket server: authenticates chargers during the handshake and runs one session each."""

from __future__ import annotations

import asyncio
import logging
import ssl
import weakref
from dataclasses import dataclass, field
from http import HTTPStatus

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

from .backend_link import SUBPROTOCOL
from .charger_auth import AuthRejected, ChargerIdentity, authenticate, parse_basic
from .charger_context import ChargerContext
from .config import ChargerConfig, Config
from .debug_server import DebugServer
from .primary_channel import PrimaryChannel
from .redact import describe_credentials
from .secondary_channel import SecondaryChannel
from .session import ChargerSession
from .state import StateStore
from .traffic_log import TrafficLog
from .transactions import TransactionMap

logger = logging.getLogger(__name__)

REPLACE_TIMEOUT_SECONDS = 15


@dataclass
class _ActiveSession:
    ws: ServerConnection
    session: ChargerSession
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class ProxyServer:
    def __init__(self, config: Config):
        self._config = config
        self._chargers = {charger_id: self._build_context(charger) for charger_id, charger in config.chargers.items()}
        self._server: Server | None = None
        self._debug = DebugServer(config.debug, self) if config.debug.enabled else None
        # Weak: a handshake can still fail after process_request (e.g. subprotocol mismatch).
        self._identities: weakref.WeakKeyDictionary[ServerConnection, ChargerIdentity] = weakref.WeakKeyDictionary()
        self._sessions: dict[str, _ActiveSession] = {}
        # Serializes session registration per charger: two simultaneous handshakes must
        # not both see "no existing session" and both start one.
        self._locks: dict[str, asyncio.Lock] = {charger_id: asyncio.Lock() for charger_id in config.chargers}

    async def start(self) -> Server:
        proxy = self._config.proxy
        server = await serve(
            self._handle,
            proxy.listen,
            proxy.port,
            process_request=self._process_request,
            subprotocols=[SUBPROTOCOL],
            ssl=self._ssl_context(),
            ping_interval=proxy.ping_interval,
            ping_timeout=proxy.ping_timeout,
            server_header=None,
        )
        self._server = server
        for context in self._chargers.values():
            context.start_background()
        if self._debug is not None:
            await self._debug.start()
        scheme = "wss" if proxy.tls_cert else "ws"
        logger.info("listening on %s://%s:%d/<chargerId>", scheme, proxy.listen, proxy.port)
        return server

    @property
    def debug_port(self) -> int | None:
        return self._debug.port if self._debug is not None else None

    async def close(self) -> None:
        if self._debug is not None:
            await self._debug.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await asyncio.gather(*(context.close() for context in self._chargers.values()))

    def session_for(self, charger_id: str) -> ChargerSession | None:
        """The charger's live session, or None while it is not connected."""
        active = self._sessions.get(charger_id)
        return active.session if active else None

    def charger_ids(self) -> tuple[str, ...]:
        return tuple(self._config.chargers)

    def _build_context(self, charger: ChargerConfig) -> ChargerContext:
        store = StateStore.for_charger(self._config.proxy.state_dir, charger.id)
        traffic = TrafficLog(charger.id, self._config.proxy.log_payloads)
        transactions = TransactionMap(store)
        secondaries = {
            backend.name: SecondaryChannel(backend, charger, store, transactions, traffic)
            for backend in self._config.secondaries
        }
        primary = PrimaryChannel(self._config.primary, charger, store, traffic)
        return ChargerContext(primary=primary, secondaries=secondaries, transactions=transactions, traffic=traffic)

    def _ssl_context(self) -> ssl.SSLContext | None:
        proxy = self._config.proxy
        if not proxy.tls_cert:
            return None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=proxy.tls_cert, keyfile=proxy.tls_key)
        return context

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        headers = request.headers
        authorization = headers.get("Authorization")
        logger.info(
            "handshake from %s path=%r %s subprotocols=%r user-agent=%r",
            _peer(connection),
            request.path,
            _credentials_summary(authorization),
            headers.get("Sec-WebSocket-Protocol"),
            headers.get("User-Agent"),
        )
        try:
            identity = authenticate(request.path, authorization, headers.get("User-Agent"), self._config.chargers)
        except AuthRejected as exc:
            logger.warning("handshake from %s rejected: %s", _peer(connection), exc)
            status = HTTPStatus(exc.status)
            response = connection.respond(status, f"{status.phrase}\n")
            if status is HTTPStatus.UNAUTHORIZED:
                response.headers["WWW-Authenticate"] = 'Basic realm="ocpp"'
            return response
        self._identities[connection] = identity
        return None

    async def _handle(self, ws: ServerConnection) -> None:
        identity = self._identities.pop(ws)
        charger_id = identity.charger.id
        async with self._locks[charger_id]:
            await self._replace_existing(charger_id)
            session = ChargerSession(ws, identity, self._config, self._chargers[charger_id])
            active = _ActiveSession(ws, session)
            self._sessions[charger_id] = active
        logger.info("%s connected (subprotocol %s)", charger_id, ws.subprotocol)
        try:
            await session.run()
        except Exception:
            logger.exception("%s session crashed", charger_id)
        finally:
            active.finished.set()
            if self._sessions.get(charger_id) is active:
                del self._sessions[charger_id]
            logger.info("%s session closed", charger_id)

    async def _replace_existing(self, charger_id: str) -> None:
        """Only one session per charger: two would both write its state file and double-forward."""
        existing = self._sessions.get(charger_id)
        if existing is None:
            return
        logger.info("%s reconnected; closing previous session", charger_id)
        await existing.ws.close(1000, "replaced by new connection")
        try:
            await asyncio.wait_for(existing.finished.wait(), REPLACE_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.error("%s previous session did not stop within %ss", charger_id, REPLACE_TIMEOUT_SECONDS)


def _peer(connection: ServerConnection) -> str:
    address = connection.remote_address
    return f"{address[0]}:{address[1]}" if address else "?"


def _credentials_summary(authorization: str | None) -> str:
    if authorization is None:
        return describe_credentials(None, None)
    try:
        credentials = parse_basic(authorization)
    except AuthRejected:
        return "unparseable Authorization header"
    return describe_credentials(credentials.username, credentials.password)
