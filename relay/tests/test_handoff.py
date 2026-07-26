"""Start a session, hand over a link, walk away (HATS-1194)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

from websockets.asyncio.client import connect

from hats_relay.__main__ import make_argv_builder
from hats_relay.broker import Broker
from hats_relay.server import serve_broker, shutdown

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]
HOST = "127.0.0.1"


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


@contextlib.asynccontextmanager
async def running_broker():
    broker = Broker(argv_for)
    server = await serve_broker(broker, host=HOST, port=0)
    port = next(iter(server.sockets)).getsockname()[1]
    try:
        yield f"ws://{HOST}:{port}", broker
    finally:
        await shutdown(server, broker)


def test_the_remote_channel_does_not_default_to_agy():
    """HATS-1188 excludes agy from the remote channel; the project default resolves to it."""
    assert make_argv_builder("ai-hats", "claude")({"role": "assistant"}) == [
        "ai-hats", "-p", "claude", "-r", "assistant",
    ]


def test_an_explicit_empty_provider_defers_to_the_project():
    assert make_argv_builder("ai-hats", "")({"role": "assistant"}) == ["ai-hats", "-r", "assistant"]


def test_a_session_outlives_the_client_that_started_it():
    """The whole point of --no-attach: create, hand over the link, close the shell."""

    async def scenario():
        async with running_broker() as (url, broker):
            async with connect(url) as ws:
                await ws.send(
                    json.dumps({"op": "create", "spec": {"role": "r"}, "cols": 90, "rows": 25})
                )
                sid = json.loads(await ws.recv())["sid"]
            # The connection is now closed, exactly as it is after --no-attach returns.
            await asyncio.sleep(0.5)
            entry = broker.get(sid)
            return sid, entry, (entry.exited if entry else "gone"), entry.client_count

    sid, entry, exited, clients = asyncio.run(scenario())
    assert entry is not None, f"session {sid} was reaped when its creator left"
    assert exited is None, f"session died with {exited} after its creator left"
    assert clients == 0


def test_a_later_client_can_attach_to_it():
    """And the handed-over link has to actually work afterwards."""

    async def scenario():
        async with running_broker() as (url, _broker):
            async with connect(url) as ws:
                await ws.send(
                    json.dumps({"op": "create", "spec": {"role": "r"}, "cols": 90, "rows": 25})
                )
                sid = json.loads(await ws.recv())["sid"]
            async with connect(url) as later:
                await later.send(
                    json.dumps({"op": "attach", "sid": sid, "cols": 90, "rows": 25})
                )
                return json.loads(await later.recv())

    reply = asyncio.run(scenario())
    assert reply.get("ok") is True, reply
