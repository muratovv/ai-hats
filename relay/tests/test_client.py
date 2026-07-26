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


# Captured in-session with --log-keys, claude under tmux 3.6a. Two rounds of guessing
# died here: the terminal speaks xterm modifyOtherKeys, not kitty CSI-u, and it reports
# Ctrl-/ as codepoint 95 ('_'), not 47 ('/').
MEASURED = {
    "ctrl-/": b"\x1b[27;5;95~",
    "f12": b"\x1b[24~",
}


@pytest.mark.parametrize("name, arriving", sorted(MEASURED.items()))
def test_detach_matches_what_the_terminal_really_sends(name, arriving):
    from hats_relay.client import DETACH_KEYS

    assert arriving in DETACH_KEYS[name], f"{name} would not fire on {arriving!r}"


def test_every_detach_key_carries_more_than_the_legacy_byte():
    """Matching only the legacy byte is how the first two attempts silently failed."""
    from hats_relay.client import DEFAULT_DETACH, DETACH_KEYS

    assert DEFAULT_DETACH in DETACH_KEYS
    for name, patterns in DETACH_KEYS.items():
        assert len(patterns) >= 2, f"{name} has a single encoding"
        assert all(patterns), name


def test_the_default_is_a_key_the_session_does_not_want():
    """Ctrl-/ erases the input line in claude, Ctrl-G opens $EDITOR, Ctrl-O toggles
    output. A detach key that steals a working binding is a bad trade."""
    from hats_relay.client import DEFAULT_DETACH

    assert not DEFAULT_DETACH.startswith("ctrl-")


def test_explicit_detach_bytes_win_over_the_guessed_table():
    """The named table is guesswork about terminal encoding; captured bytes are not."""
    from hats_relay.client import resolve_detach

    args = build_parser().parse_args(["ws://x", "--detach-bytes", "1b 4f 53"])
    assert resolve_detach(args) == (b"\x1bOS",)

    bad = build_parser().parse_args(["ws://x", "--detach-bytes", "zz"])
    with pytest.raises(SystemExit, match="not hex"):
        resolve_detach(bad)


def test_keys_probe_needs_no_url():
    """The probe exists to diagnose a broker you cannot reach a shell on."""
    args = build_parser().parse_args(["--keys"])
    assert args.keys is True
    assert args.url is None


def test_raw_terminal_restores_even_on_an_exception():
    """The failure mode is a shell left in raw mode after a crash — unusable."""
    with pytest.raises(RuntimeError), RawTerminal(fd=0):
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "mode, what",
    [
        (b"\x1b[?1000l", "mouse clicks"),
        (b"\x1b[?1002l", "mouse drag"),
        (b"\x1b[?1003l", "any-motion mouse"),
        (b"\x1b[?1006l", "SGR mouse coordinates"),
        (b"\x1b[?1004l", "focus reporting"),
        (b"\x1b[?2004l", "bracketed paste"),
        (b"\x1b[=0;1u", "kitty keyboard flags"),
        (b"\x1b[>4;0m", "modifyOtherKeys"),
        (b"\x1b[?1049l", "alternate screen"),
        (b"\x1b[?25h", "cursor visibility"),
    ],
)
def test_detach_turns_off_every_mode_the_session_enabled(mode, what):
    """The session keeps running after a detach, so it never sends these disables. Any
    one left on damages the terminal the user goes back to — mouse reporting most
    visibly, which makes every pointer move spew escape sequences."""
    from hats_relay.client import TERM_RESTORE

    assert mode in TERM_RESTORE, f"detach leaves {what} enabled"


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
