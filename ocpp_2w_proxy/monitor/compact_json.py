"""Compact JSON text, as the monitor stores and serves it.

``json.dumps`` with non-default arguments builds a new encoder on every call; one shared encoder does not. Its
output is identical, and it keeps no state between calls, so the writer and the reader threads can share it.
"""

from __future__ import annotations

import json

dumps = json.JSONEncoder(separators=(",", ":")).encode


def dumps_bytes(body: object) -> bytes:
    return dumps(body).encode()
