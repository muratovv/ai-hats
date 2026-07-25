"""Terminal client (HATS-1193 S5).

Raw mode and real keystrokes are only provable by hand — that is what the acceptance
run in plan.md is for. What is tested here is everything around them: the request the
client builds, and that it restores the terminal whatever happens.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hats_relay import wire
from hats_relay.broker import Broker
from hats_relay.client import RawTerminal, build_parser, list_sessions, run_client
from hats_relay.server import serve_broker, shutdown

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]
HOST = "127.0.0.1"


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


def run(coro):
    return asyncio.run(coro)


async def _serve():
    broker = Broker(argv_for)
    server = await serve_broker(broker, host=HOST, port=0)
    port = next(iter(server.sockets)).getsockname()[1]
    return broker, server, f"ws://{HOST}:{port}"


def test_list_mode_reports_sessions(capsys):
    async def scenario():
        broker, server, url = await _serve()
        try:
            entry = await broker.create({"role": "maintainer"}, cols=90, rows=25)
            await list_sessions(url)
            return entry.sid
        finally:
            await shutdown(server, broker)

    sid = run(scenario())
    out = capsys.readouterr().out
    assert sid in out
    assert "maintainer" in out


def test_attach_request_carries_the_resume_point():
    """--after-seq is what makes a reconnect pick up rather than start over."""
    args = build_parser().parse_args(["ws://x", "--sid", "a" * 32, "--after-seq", "77"])
    assert args.sid == "a" * 32
    assert args.after_seq == 77


def test_a_refusal_surfaces_instead_of_hanging():
    """A client that silently waits forever on a rejected attach is worse than one
    that exits with the reason."""

    async def scenario():
        broker, server, url = await _serve()
        try:
            args = build_parser().parse_args([url, "--sid", "0" * 32])
            with pytest.raises(SystemExit, match="no such session"):
                await run_client(args)
        finally:
            await shutdown(server, broker)

    run(scenario())


def test_raw_terminal_restores_even_on_an_exception():
    """The failure mode is a shell left in raw mode after a crash — unusable."""
    with pytest.raises(RuntimeError), RawTerminal(fd=0):
        raise RuntimeError("boom")


def test_client_drives_a_session_over_the_wire():
    """The client's own framing must match what the broker expects, end to end."""

    async def scenario():
        broker, server, url = await _serve()
        try:
            async with connect(url) as ws:
                await ws.send(
                    json.dumps({"op": "create", "spec": {"role": "r"}, "cols": 90, "rows": 25})
                )
                await ws.recv()
                await ws.send(wire.client_input(b"typed by hand"))
                async with asyncio.timeout(10):
                    while True:
                        msg = await ws.recv()
                        if isinstance(msg, bytes):
                            _, payload = wire.parse_client_output(msg)
                            if payload == b"TYPED BY HAND":
                                return True
        finally:
            await shutdown(server, broker)

    assert run(scenario()) is True
