"""The page rides the broker's own port (HATS-1194 S1)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hats_relay import web
from hats_relay.broker import Broker
from hats_relay.server import serve_broker, shutdown

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]
HOST = "127.0.0.1"


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


TOKEN = "asset-token"  # noqa: S105 — a fixture, not a credential


@contextlib.asynccontextmanager
async def running_broker(*, web_client: bool):
    broker = Broker(argv_for)
    # No broker starts tokenless (test_token.py owns that rule); these tests are about
    # the asset routing that sits in front of it.
    server = await serve_broker(broker, host=HOST, port=0, web_client=web_client, token=TOKEN)
    port = next(iter(server.sockets)).getsockname()[1]
    try:
        yield port, broker
    finally:
        await shutdown(server, broker)


async def _fetch(port: int, path: str) -> tuple[int, str, bytes]:
    """GET over plain HTTP, off the loop so the server can answer."""

    def get():
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=10) as response:
                return response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    return await asyncio.to_thread(get)


def test_assets_load_with_the_page_at_the_root():
    assets = web.load_assets()
    assert assets["/"] == assets["/index.html"]
    assert assets["/index.html"][1].startswith("text/html")
    assert assets["/vendor/xterm.js"][1].startswith("text/javascript")
    assert assets["/vendor/xterm.css"][1].startswith("text/css")


def test_only_servable_suffixes_are_routed():
    """The vendored LICENSE sits in the asset dir; the table must not pick it up."""
    assert any((web.ASSET_DIR / "vendor").glob("LICENSE*"))
    assert not [route for route in web.load_assets() if "LICENSE" in route]


@pytest.mark.parametrize(
    "path, content_type, needle",
    [
        ("/", "text/html", b"<title>hats-relay</title>"),
        ("/index.html", "text/html", b"/vendor/xterm.js"),
        ("/app.js", "text/javascript", b"PROTOCOL_VERSION"),
        ("/vendor/xterm.css", "text/css", b".xterm"),
    ],
)
def test_serves_each_asset(path, content_type, needle):
    async def scenario():
        async with running_broker(web_client=True) as (port, _broker):
            return await _fetch(port, path)

    status, got_type, body = asyncio.run(scenario())
    assert status == 200
    assert got_type.startswith(content_type)
    assert needle in body


def test_query_string_is_not_part_of_the_route():
    """The browser arrives with ?sid=…; routing must ignore it."""

    async def scenario():
        async with running_broker(web_client=True) as (port, _broker):
            return await _fetch(port, "/?sid=deadbeef")

    status, _type, body = asyncio.run(scenario())
    assert status == 200
    assert b"<title>hats-relay</title>" in body


def test_unknown_path_is_a_404():
    async def scenario():
        async with running_broker(web_client=True) as (port, _broker):
            return await _fetch(port, "/nope.js")

    status, _type, _body = asyncio.run(scenario())
    assert status == 404


def test_traversal_cannot_reach_the_source_tree():
    """Sent raw: urllib would collapse the `..` itself and test nothing."""

    def raw_get(port: int, target: str) -> bytes:
        with socket.create_connection((HOST, port), timeout=10) as sock:
            sock.sendall(f"GET {target} HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode())
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
            return b"".join(chunks)

    async def scenario():
        async with running_broker(web_client=True) as (port, _broker):
            return await asyncio.to_thread(raw_get, port, "/../server.py")

    response = asyncio.run(scenario())
    assert b"404" in response.split(b"\r\n", 1)[0]
    assert b"serve_broker" not in response


def test_websocket_upgrade_still_works_with_the_page_served():
    """Serving HTTP must not shadow the handshake on the same port."""

    async def scenario():
        async with running_broker(web_client=True) as (port, _broker):
            async with connect(f"ws://{HOST}:{port}") as ws:
                await ws.send(json.dumps({"op": "list", "token": TOKEN}))
                return await ws.recv()

    assert '"ok"' in asyncio.run(scenario())


def test_without_web_the_page_is_not_served():
    """--web is opt-in: the default broker exposes no HTTP surface."""

    async def scenario():
        async with running_broker(web_client=False) as (port, _broker):
            return await _fetch(port, "/")

    status, _type, _body = asyncio.run(scenario())
    assert status != 200
