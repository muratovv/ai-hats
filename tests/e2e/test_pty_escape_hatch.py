"""e2e (HATS-679)

flow:   a developer pressing triple Ctrl-C during a wedged PTY session
cmds:
    # during interactive PTY session when process hangs
    ai-hats execute -r assistant
expect: triple Ctrl-C signals escape hatch, force-exiting wedged session with exit 130
why:    without parent escape hatch, a process ignoring SIGINT locks up terminal session
        indefinitely
"""

from __future__ import annotations

import os
import select
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.surfaces

_REPO_ROOT = Path(__file__).resolve().parents[2]


# Wedged provider: swallows SIGINT/SIGQUIT, replies "API Error" to any line,
# and NEVER closes stdout / exits. SIGTERM/SIGKILL still work, so
# bounded_proc_shutdown can reap it once the hatch fires.
FAKE_PROVIDER_SOURCE = textwrap.dedent(
    """\
    import signal, sys, time
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)
    sys.stdout.write("fake-claude ready> ")
    sys.stdout.flush()
    while True:
        line = sys.stdin.readline()
        if line == "":
            time.sleep(0.05)
            continue
        sys.stdout.write("\\r\\nAPI Error: 400 `thinking` blocks cannot be modified\\r\\nready> ")
        sys.stdout.flush()
    """
)


# Driver: runs the REAL _pty_spawn against the wedged provider and prints
# whatever it returns between sentinels. Inserts the worktree src + package
# srcs FIRST so the editable installs (which point at the main checkout) do
# not shadow this branch's code (HATS-863).
DRIVER_SOURCE = textwrap.dedent(
    """\
    import os, sys
    sys.path[:0] = {src_roots!r}
    from ai_hats.runtime import WrapRunner

    class _StubSession:
        def log_trace(self, *a, **k):
            pass
        def log_sys(self, *a, **k):  # HATS-948: semantic tag methods
            pass
        def log_sub(self, *a, **k):
            pass
        def log_res(self, *a, **k):
            pass

    class _StubTracer:
        def __init__(self):
            self.session = _StubSession()
        def make_master_read(self):
            return lambda fd: os.read(fd, 65536)
        def make_stdin_read(self):
            return lambda fd: os.read(fd, 65536)

    runner = WrapRunner.__new__(WrapRunner)
    rc = WrapRunner._pty_spawn(
        runner, [sys.executable, {provider!r}], {{}}, _StubTracer()
    )
    sys.stdout.write("\\r\\n__DRIVER_EXIT__ %s\\r\\n" % rc)
    sys.stdout.flush()
    """
)


def _drain(fd: int, seconds: float) -> str:
    out = b""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            r, _, _ = select.select([fd], [], [], 0.1)
        except OSError:
            break
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out.decode(errors="replace")


def _read_until(fd: int, needle: str, seconds: float) -> str:
    out = ""
    end = time.monotonic() + seconds
    while time.monotonic() < end and needle not in out:
        try:
            r, _, _ = select.select([fd], [], [], 0.1)
        except OSError:
            break
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk.decode(errors="replace")
    return out


@pytest.mark.integration
def test_triple_ctrl_c_force_exits_wedged_provider(tmp_path):
    """Real _pty_spawn + wedged child: batched 3×Ctrl-C → returns 130, self-exits."""
    from ptyprocess import PtyProcess

    provider = tmp_path / "fake_provider.py"
    provider.write_text(FAKE_PROVIDER_SOURCE)
    driver = tmp_path / "driver.py"
    from _helpers.env import checkout_pythonpath

    src_roots = checkout_pythonpath(_REPO_ROOT).split(os.pathsep)
    driver.write_text(DRIVER_SOURCE.format(src_roots=src_roots, provider=str(provider)))

    proc = PtyProcess.spawn([sys.executable, str(driver)], dimensions=(24, 80))
    try:
        # Let the inner provider come up and establish the error loop.
        _drain(proc.fd, 1.5)
        proc.write(b"hello\r")
        _drain(proc.fd, 0.5)

        # The escape gesture — all three Ctrl-C in ONE write (timing-independent).
        proc.write(b"\x03\x03\x03")

        transcript = _read_until(proc.fd, "__DRIVER_EXIT__", seconds=10.0)

        assert "__DRIVER_EXIT__ 130" in transcript, (
            "escape-hatch did not force-exit with 130 — wedged session never "
            f"escaped (fail-under-revert). transcript tail:\n{transcript[-400:]}"
        )
        # The driver printed its sentinel and is exiting on its own — no
        # external SIGKILL was needed to free the user (the whole point).
        death_deadline = time.monotonic() + 3.0
        while time.monotonic() < death_deadline and proc.isalive():
            time.sleep(0.05)
        assert not proc.isalive(), "driver still alive after force-exit sentinel"
    finally:
        # Teardown: reap the driver (and, transitively, any inner survivor).
        if proc.isalive():
            try:
                proc.kill(signal.SIGKILL)
            except Exception:  # noqa: S110 — teardown; the zombie drain below follows
                pass
        # Drain any orphaned zombies politely so `pytest -x` loops stay clean.
        while True:
            try:
                reaped, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped == 0:
                break
