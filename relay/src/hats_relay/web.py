"""Static assets for the browser client.

The broker already owns a port, and websockets calls ``process_request`` for every
request before deciding to upgrade — so the page rides the same listener rather than a
second server. The request path is only ever a dict key, never a filesystem path: that
is what makes traversal impossible here rather than merely guarded against.
"""

from __future__ import annotations

import http
from pathlib import Path

from websockets.datastructures import Headers
from websockets.http11 import Response

ASSET_DIR = Path(__file__).parent / "web"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def load_assets() -> dict[str, tuple[bytes, str]]:
    """Read the servable files once; the set is fixed at build time."""
    table: dict[str, tuple[bytes, str]] = {}
    for path in sorted(ASSET_DIR.rglob("*")):
        content_type = CONTENT_TYPES.get(path.suffix)
        if content_type is None or not path.is_file():
            continue
        table["/" + path.relative_to(ASSET_DIR).as_posix()] = (path.read_bytes(), content_type)
    if "/index.html" in table:
        table["/"] = table["/index.html"]
    return table


def make_process_request(assets: dict[str, tuple[bytes, str]] | None = None):
    """Build the callback that serves the page and lets upgrades through."""
    table = load_assets() if assets is None else assets

    def process_request(connection, request) -> Response | None:
        # Returning None hands the request back to the WebSocket handshake.
        if request.headers.get("Upgrade"):
            return None
        body_and_type = table.get(request.path.split("?", 1)[0])
        if body_and_type is None:
            return connection.respond(http.HTTPStatus.NOT_FOUND, "not found\n")
        body, content_type = body_and_type
        headers = Headers(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                # The page and the broker ship together; a stale cached page against a
                # newer broker is a protocol mismatch with no visible cause.
                "Cache-Control": "no-store",
            }
        )
        return Response(200, "OK", headers, body)

    return process_request
