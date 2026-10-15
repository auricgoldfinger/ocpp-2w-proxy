"""How a frame's payload is stored: its JSON text as UTF-8, zlib-compressed once it is large enough to gain.

Small payloads (a Heartbeat, most replies) stay as they are. Compressing them gains nothing, and each zlib call
in the write worker drops and retakes the GIL, a moment in which the event loop can stall behind the worker.
The two forms tell themselves apart: a zlib stream starts with ``x`` (0x78), which no JSON text does.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

COMPRESS_FROM = 200  # bytes
_ZLIB_HEADER = b"x"


def encode(text: str) -> bytes:
    data = text.encode()
    return zlib.compress(data) if len(data) >= COMPRESS_FROM else data


def decode(blob: bytes) -> Any:
    """The payload a stored blob holds; a blob written before small payloads were kept as is was always zlib."""
    return json.loads(zlib.decompress(blob) if blob[:1] == _ZLIB_HEADER else blob)
