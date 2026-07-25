"""Session spawn and lifecycle (HATS-1193 S2)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from hats_relay.session import Session

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]


def run(coro):
    return asyncio.run(coro)


async def _spawn(**kw) -> Session:
    return await Session.spawn(FAKE_CHILD, cols=kw.pop("cols", 100), rows=kw.pop("rows", 30), **kw)


def test_child_receives_the_seam_fds():
    """The whole contract starts here: if the fds do not arrive, nothing else can work."""

    async def scenario():
        session = await _spawn()
        try:
            return json.loads(await asyncio.wait_for(session.read(), timeout=10))
        finally:
            await session.close()

    hello = run(scenario())
    assert hello["hello"] is True
    assert hello["pid"] > 0


def test_child_starts_at_the_requested_size():
    """Guards the 24x80 fallback: ai-hats sizes from os.get_terminal_size(), which
    raises without a real PTY and cannot be seeded through COLUMNS/LINES."""

    async def scenario():
        session = await _spawn(cols=137, rows=41)
        try:
            return json.loads(await asyncio.wait_for(session.read(), timeout=10))
        finally:
            await session.close()

    assert run(scenario())["term_size"] == "137x41"


def test_child_runs_in_its_own_process_group():
    """Teardown signals the group, so the child must lead one rather than join ours."""

    async def scenario():
        session = await _spawn()
        try:
            hello = json.loads(await asyncio.wait_for(session.read(), timeout=10))
            return hello["pid"], hello["pgid"]
        finally:
            await session.close()

    pid, pgid = run(scenario())
    assert pid == pgid
    assert pgid != os.getpgid(0)


def test_input_and_output_both_traverse_the_seam():
    """Output already proved itself above; this closes the loop on input."""

    async def scenario():
        session = await _spawn()
        try:
            await asyncio.wait_for(session.read(), timeout=10)  # hello
            await session.send_input(b"round trip")
            return await asyncio.wait_for(session.read(), timeout=10)
        finally:
            await session.close()

    assert run(scenario()) == b"ROUND TRIP"


def test_resize_reaches_the_child_as_a_control_frame():
    """The resync primitive has to arrive as CTRL, not as keystrokes in the terminal."""

    async def scenario():
        session = await _spawn()
        try:
            await asyncio.wait_for(session.read(), timeout=10)  # hello
            await session.resize(120, 40)
            return await asyncio.wait_for(session.read(), timeout=10)
        finally:
            await session.close()

    assert json.loads(run(scenario()).removeprefix(b"CTRL:")) == {
        "resize": {"cols": 120, "rows": 40}
    }


def test_child_exit_surfaces_as_eof():
    """If the parent keeps its copy of the child fd, this hangs forever instead."""

    async def scenario():
        session = await _spawn()
        try:
            await asyncio.wait_for(session.read(), timeout=10)  # hello
            await session.send_input(b"quit\n")
            return await asyncio.wait_for(session.read(), timeout=10)
        finally:
            await session.close()

    assert run(scenario()) is None


def test_idle_session_burns_no_cpu():
    """The child's own stdio is drained and discarded. Doing that by polling a
    non-blocking fd costs a core per session and is invisible in every other test."""

    async def scenario():
        session = await _spawn()
        try:
            await asyncio.wait_for(session.read(), timeout=10)  # hello
            before = time.process_time()
            await asyncio.sleep(0.5)
            return time.process_time() - before
        finally:
            await session.close()

    assert run(scenario()) < 0.05


def test_close_reaps_the_child():
    """A broker that leaks processes is worse than one that crashes."""

    async def scenario():
        session = await _spawn()
        await asyncio.wait_for(session.read(), timeout=10)
        pid = session.pid
        await session.close()
        return pid, session.returncode

    pid, rc = run(scenario())
    assert rc is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
