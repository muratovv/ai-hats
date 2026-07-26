"""The token handshake and the URL handoff (HATS-1194 S2)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hats_relay import protocol
from hats_relay.broker import Broker
from hats_relay.client import browser_url
from hats_relay.server import serve_broker, shutdown

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]
HOST = "127.0.0.1"
TOKEN = "s3cret-token"


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


@contextlib.asynccontextmanager
async def running_broker(**kwargs):
    broker = Broker(argv_for)
    server = await serve_broker(broker, host=HOST, port=0, **kwargs)
    port = next(iter(server.sockets)).getsockname()[1]
    try:
        yield f"ws://{HOST}:{port}", broker
    finally:
        await shutdown(server, broker)


async def _first_reply(url: str, request: dict) -> dict:
    async with connect(url) as ws:
        await ws.send(json.dumps(request))
        return json.loads(await ws.recv())


def test_token_rides_every_op():
    for request in (
        {"op": "list", "token": "t"},
        {"op": "kill", "sid": "s", "token": "t"},
        {"op": "resize", "cols": 10, "rows": 10, "token": "t"},
        {"op": "create", "spec": {"role": "r"}, "cols": 10, "rows": 10, "token": "t"},
        {"op": "attach", "sid": "s", "cols": 10, "rows": 10, "token": "t"},
    ):
        assert protocol.parse_control(json.dumps(request)).token == "t", request


def test_a_non_string_token_is_refused():
    with pytest.raises(protocol.ProtocolError, match="token must be a string"):
        protocol.parse_control(json.dumps({"op": "list", "token": 42}))


@pytest.mark.parametrize(
    "request_",
    [
        {"op": "list"},
        {"op": "list", "token": "wrong"},
        {"op": "create", "spec": {"role": "r"}, "cols": 10, "rows": 10},
        {"op": "attach", "sid": "whatever", "cols": 10, "rows": 10, "token": "wrong"},
    ],
)
def test_no_op_answers_without_the_token(request_):
    """`list` included: the sids it returns are themselves the capability."""

    async def scenario():
        async with running_broker(token=TOKEN) as (url, _broker):
            return await _first_reply(url, request_)

    assert asyncio.run(scenario()) == {"error": "unauthorized"}


def test_the_right_token_gets_through():
    async def scenario():
        async with running_broker(token=TOKEN) as (url, _broker):
            return await _first_reply(url, {"op": "list", "token": TOKEN})

    assert asyncio.run(scenario())["ok"] is True


def test_no_token_configured_means_no_check():
    """The existing LAN posture is unchanged when the operator sets no token."""

    async def scenario():
        async with running_broker() as (url, _broker):
            return await _first_reply(url, {"op": "list"})

    assert asyncio.run(scenario())["ok"] is True


def test_web_without_a_token_refuses_to_start():
    """Serving a page relaxes the Origin check; the token is what replaces it."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            with pytest.raises(ValueError, match="--web requires a token"):
                await serve_broker(broker, host=HOST, port=0, web_client=True)
        finally:
            await broker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "ws_url, token, expected",
    [
        ("ws://10.0.0.5:8787", "t", "http://10.0.0.5:8787/?sid=abc&token=t"),
        ("ws://10.0.0.5:8787/", "t", "http://10.0.0.5:8787/?sid=abc&token=t"),
        ("wss://relay.example:443", "t", "https://relay.example:443/?sid=abc&token=t"),
        ("ws://10.0.0.5:8787", "", "http://10.0.0.5:8787/?sid=abc"),
        ("ws://10.0.0.5:8787", "a b&c", "http://10.0.0.5:8787/?sid=abc&token=a+b%26c"),
    ],
)
def test_browser_url_is_openable(ws_url, token, expected):
    assert browser_url(ws_url, "abc", token) == expected
