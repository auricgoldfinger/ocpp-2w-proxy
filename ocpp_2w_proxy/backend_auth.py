"""Build the HTTP headers the proxy presents to a backend, per that backend's auth mode."""

from __future__ import annotations

import base64

from .charger_auth import ChargerIdentity
from .config import AuthMode, BackendConfig


class MissingChargerCredentials(Exception):
    pass


def backend_headers(backend: BackendConfig, backend_charger_id: str, identity: ChargerIdentity) -> dict[str, str]:
    match backend.auth:
        case AuthMode.NONE:
            return {}
        case AuthMode.FORWARD:
            if identity.authorization_header is None:
                raise MissingChargerCredentials(
                    f"{backend.name} uses auth='forward' but the charger sent no Authorization header"
                )
            return {"Authorization": identity.authorization_header}
        case AuthMode.BASIC:
            token = base64.b64encode(f"{backend_charger_id}:{backend.password}".encode()).decode("ascii")
            return {"Authorization": f"Basic {token}"}


def backend_url(backend: BackendConfig, backend_charger_id: str) -> str:
    return f"{backend.url}/{backend_charger_id}"
