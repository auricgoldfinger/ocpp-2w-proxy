"""Authenticate a connecting charger from its WebSocket handshake (path + HTTP Basic auth).

OCPP 1.6 Security Profile 1/2: the charger connects to <url>/<chargeBoxId> and sends
Basic auth with its id as username; the password is optional on the charger side.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .config import CHARGER_ID_PATTERN, ChargerConfig


class AuthRejected(Exception):
    """The handshake must be refused. The message is for the log, never sent to the client."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass(frozen=True)
class ChargerIdentity:
    charger: ChargerConfig
    authorization_header: str | None
    user_agent: str | None


def charger_id_from_path(path: str) -> str:
    segments = [s for s in urlsplit(path).path.split("/") if s]
    if not segments:
        raise AuthRejected(404, "no charger id in path")
    charger_id = unquote(segments[-1])
    if not CHARGER_ID_PATTERN.match(charger_id):
        raise AuthRejected(404, "charger id in path has an invalid format")
    return charger_id


def parse_basic(header: str) -> Credentials:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise AuthRejected(401, "Authorization header is not Basic")
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("latin-1")
    except (binascii.Error, ValueError) as exc:
        raise AuthRejected(401, "Authorization header is not valid base64") from exc
    username, separator, password = decoded.partition(":")
    if not separator:
        raise AuthRejected(401, "Authorization header has no ':' separator")
    return Credentials(username, password)


def authenticate(
    path: str,
    authorization: str | None,
    user_agent: str | None,
    chargers: Mapping[str, ChargerConfig],
) -> ChargerIdentity:
    charger_id = charger_id_from_path(path)
    charger = chargers.get(charger_id)
    if charger is None:
        raise AuthRejected(404, f"charger {charger_id!r} is not on the allowlist")

    credentials = parse_basic(authorization) if authorization else None
    if credentials and credentials.username != charger_id:
        raise AuthRejected(401, f"username does not match charger id {charger_id!r}")
    if charger.password is not None:
        # The header was decoded as latin-1, so re-encoding yields the exact bytes the charger sent.
        supplied = credentials.password.encode("latin-1") if credentials else b""
        if not hmac.compare_digest(supplied, charger.password.encode("utf-8")):
            raise AuthRejected(401, f"wrong or missing password for charger {charger_id!r}")

    return ChargerIdentity(charger, authorization, user_agent)
