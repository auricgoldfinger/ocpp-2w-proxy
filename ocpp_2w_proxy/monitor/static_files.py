"""The monitor UI's static assets, served from a whitelist read once at startup.

Only files found under the static directory at startup can ever be served, each under its exact URL path, so
no request path is ever mapped onto the file system and path traversal is impossible by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..minihttp import Content, Request

DEFAULT_ROOT = Path(__file__).parent / "static"

CONTENT_TYPES: Mapping[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
# Test suites, their fixtures and tooling live beside the UI but are never part of it.
EXCLUDED_DIRECTORIES = frozenset({"tests", "test", "fixtures", "node_modules"})
EXCLUDED_SUFFIXES = (".test.js", ".spec.js")
INDEX = "index.html"

SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-cache"),
    ("Referrer-Policy", "no-referrer"),
)


class StaticFiles:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self._files = _whitelist(root)

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self._files)

    async def handle(self, request: Request) -> Content:
        if request.method != "GET":
            return _text(405, "use GET")
        found = self._files.get(request.path)
        if found is None:
            return _text(404, "not found")
        body, content_type = found
        return Content(200, body, content_type, SECURITY_HEADERS)


def _whitelist(root: Path) -> dict[str, tuple[bytes, str]]:
    """URL path → (bytes, content type) for every servable file under ``root``; empty if there is none."""
    if not root.is_dir():
        return {}
    files: dict[str, tuple[bytes, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _servable(path, relative):
            files[f"/{relative.as_posix()}"] = (path.read_bytes(), CONTENT_TYPES[path.suffix])
    if f"/{INDEX}" in files:
        files["/"] = files[f"/{INDEX}"]
    return files


def _servable(path: Path, relative: Path) -> bool:
    if path.is_symlink() or not path.is_file() or path.suffix not in CONTENT_TYPES:
        return False
    if any(part in EXCLUDED_DIRECTORIES or part.startswith(".") for part in relative.parts):
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES)


def _text(status: int, message: str) -> Content:
    return Content(status, message.encode(), "text/plain; charset=utf-8", SECURITY_HEADERS)
