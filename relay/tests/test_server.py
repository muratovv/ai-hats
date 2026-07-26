"""End-to-end over a real WebSocket (HATS-1193 S3)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from conftest import RELAY_TOKEN, ctl

from hats_relay import wire
from hats_relay.broker import Broker
from hats_relay.server import serve_broker, shutdown

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]
HOST = "127.0.0.1"


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


def run(coro):
    return asyncio.run(coro)


@contextlib.asynccontextmanager
async def running_broker():
    broker = Broker(argv_for)
    server = await serve_broker(broker, host=HOST, port=0, token=RELAY_TOKEN)
    port = next(iter(server.sockets)).getsockname()[1]
    try:
        yield f"ws://{HOST}:{port}", broker
    finally:
        await shutdown(server, broker)


async def _recv_until_binary(ws, timeout=10.0):
    """Skip control replies and return the next terminal-bytes message."""
    async with asyncio.timeout(timeout):
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                return wire.parse_client_output(msg)


def test_create_then_drive_a_session():
    async def scenario():
        async with running_broker() as (url, _broker):
            async with connect(url) as ws:
                await ws.send(ctl(op="create", spec={"role": "r"}, cols=90, rows=25))
                ack = json.loads(await ws.recv())
                _, hello = await _recv_until_binary(ws)
                await ws.send(wire.client_input(b"drive me"))
                _, echo = await _recv_until_binary(ws)
                return ack, json.loads(hello), echo

    ack, hello, echo = run(scenario())
    assert ack["ok"] is True and len(ack["sid"]) == 32
    assert hello["term_size"] == "90x25"
    assert echo == b"DRIVE ME"


def test_an_upgrade_carrying_origin_is_rejected():
    """WebSockets are outside the Same-Origin Policy, so without this any page the
    operator's own browser loads could open ws:// and type into a live agent."""

    async def scenario():
        async with running_broker() as (url, _broker):
            with pytest.raises(InvalidStatus) as caught:
                async with connect(url, additional_headers={"Origin": "http://evil.example"}):
                    pass
            return caught.value.response.status_code

    assert run(scenario()) == 403


def test_a_second_client_attaches_and_gets_the_screen_back():
    """Cold attach is the primary flow: the joiner must not stare at a blank screen."""

    async def scenario():
        async with running_broker() as (url, _broker):
            async with connect(url) as first:
                await first.send(ctl(op="create", spec={"role": "r"}, cols=90, rows=25))
                sid = json.loads(await first.recv())["sid"]
                await _recv_until_binary(first)  # hello

                async with connect(url) as second:
                    await second.send(ctl(op="attach", sid=sid, cols=90, rows=25))
                    ack = json.loads(await second.recv())
                    # The resync toggle reaches the child as CTRL frames; the fake child
                    # echoes them, which is our proof the repaint nudge was delivered.
                    seen = []
                    async with asyncio.timeout(10):
                        while len(seen) < 3:
                            _, payload = await _recv_until_binary(second)
                            seen.append(payload)
                    return ack, seen

    ack, seen = run(scenario())
    assert ack["ok"] is True
    ctrls = [json.loads(p.removeprefix(b"CTRL:")) for p in seen if p.startswith(b"CTRL:")]
    assert {"resize": {"cols": 89, "rows": 25}} in ctrls
    assert {"resize": {"cols": 90, "rows": 25}} in ctrls


def test_list_reports_live_sessions():
    async def scenario():
        async with running_broker() as (url, _broker):
            async with connect(url) as ws:
                await ws.send(ctl(op="create", spec={"role": "alpha"}, cols=80, rows=24))
                await ws.recv()
                async with connect(url) as other:
                    await other.send(ctl(op="list"))
                    return json.loads(await other.recv())

    reply = run(scenario())
    assert reply["ok"] is True
    assert [s["spec"]["role"] for s in reply["sessions"]] == ["alpha"]


def test_a_refused_control_message_does_not_open_a_session():
    async def scenario():
        async with running_broker() as (url, broker):
            async with connect(url) as ws:
                await ws.send(ctl(op="create", spec={"role": "-x"}, cols=80, rows=24))
                reply = json.loads(await ws.recv())
            return reply, broker.list()

    reply, sessions = run(scenario())
    assert "error" in reply
    assert sessions == []


def test_a_session_that_fails_to_start_tells_the_client_why():
    """The child writes its refusal to its OWN stdio, which the broker does not relay.
    Discarding that outright is how a session dies silently and reports 'running'."""

    async def scenario():
        async with running_broker() as (url, broker):
            async with connect(url) as ws:
                await ws.send(ctl(op="create", spec={"role": "fail-to-start"}, cols=90, rows=25))
                await ws.recv()  # ack
                async with asyncio.timeout(10):
                    while True:
                        msg = await ws.recv()
                        if isinstance(msg, str) and "event" in msg:
                            return json.loads(msg), broker.list()

    exit_event, listing = run(scenario())
    assert exit_event["event"] == "exit"
    assert exit_event["returncode"] == 3
    assert "Role 'nope' not found" in exit_event["detail"]
    assert listing[0]["exited"] == 3, "a dead session must not report itself as running"


def test_shutdown_is_prompt_even_with_a_client_that_stopped_reading():
    """A relay you cannot stop is an operational trap: the close handshake wants to
    drain, and a peer that is not reading has nowhere to drain to."""

    async def scenario():
        broker = Broker(argv_for)
        server = await serve_broker(broker, host=HOST, port=0, token=RELAY_TOKEN)
        port = next(iter(server.sockets)).getsockname()[1]

        # max_queue=1 makes the client stop reading its socket almost immediately.
        async with connect(f"ws://{HOST}:{port}", max_queue=1) as ws:
            await ws.send(ctl(op="create", spec={"role": "r"}, cols=90, rows=25))
            for _ in range(200):
                await ws.send(wire.client_input(b"x" * 4096))
            await asyncio.sleep(0.5)  # let the backlog pile up unread

            started = asyncio.get_running_loop().time()
            await shutdown(server, broker)
            return asyncio.get_running_loop().time() - started

    assert run(scenario()) < 5.0


def test_binding_an_interface_is_mandatory():
    """Exposure must be a decision; a default would make it an accident. Still required
    now that a token is too — the token says who may drive, not who may reach."""
    from hats_relay.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--port", "8787"])

    async def scenario():
        with pytest.raises(ValueError, match="host is required"):
            await serve_broker(Broker(argv_for), host="", port=0)

    run(scenario())


def test_attach_to_an_unknown_session_is_refused():
    async def scenario():
        async with running_broker() as (url, _broker):
            async with connect(url) as ws:
                await ws.send(ctl(op="attach", sid="0" * 32, cols=80, rows=24))
                return json.loads(await ws.recv())

    assert "error" in run(scenario())
