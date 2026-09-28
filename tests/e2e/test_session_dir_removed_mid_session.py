"""e2e (HATS-2048)

flow:   an operator approves a removal that takes the session's own run dir
        with it (rm -rf .agent, git clean -xfd, removing the project) while
        the interactive session is running
cmds:
    ai-hats -r maintainer
    # the agent runs, and the operator approves:
    rm -rf .agent
expect: the session keeps running and the keyboard stays live; the trace stops
        and says so once after the session; when the surface exits, the outer
        terminal gets its key-encoding modes reset, so the shell takes a line
why:    the relay read a trace-write error as a PTY failure and killed the
        surface (exit 124), and a killed surface never turns off the kitty
        keyboard protocol it enabled on the outer terminal — the pane was left
        sending Enter as ESC[13u, which no shell submits
"""  # comment-length: allow — the e2e catalog header format

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

# Stand-in surface: turns on the key-encoding modes claude enables on the outer
# terminal, removes the session dir on the first input line, then keeps talking.
FAKE_SURFACE_SOURCE = textwrap.dedent(
    """\
    import shutil, sys
    out = sys.stdout
    out.write("\\x1b[>5u\\x1b[>4;2mfake-surface ready\\r\\n")
    out.flush()
    sys.stdin.readline()
    shutil.rmtree(sys.argv[1])
    out.write("after-rm\\r\\n")
    out.flush()
    line = sys.stdin.readline().strip()
    out.write("got: %s\\r\\nbye\\r\\n" % line)
    out.flush()
    """
)

# Driver: the REAL _pty_spawn with the REAL tracer over a real session dir.
# Worktree srcs go first so an editable install of another checkout does not
# shadow this branch.
DRIVER_SOURCE = textwrap.dedent(
    """\
    import sys
    sys.path[:0] = {src_roots!r}
    from pathlib import Path
    from ai_hats.runtime import WrapRunner
    from ai_hats_observe import Session, SidecarTracer

    session_dir = Path({session_dir!r})
    session_dir.mkdir(parents=True)
    tracer = SidecarTracer(Session(session_id="removed-mid-session", session_dir=session_dir))
    runner = WrapRunner.__new__(WrapRunner)
    rc = WrapRunner._pty_spawn(
        runner, [sys.executable, {surface!r}, str(session_dir)], {{}}, tracer
    )
    sys.stdout.write("\\r\\n__DRIVER_EXIT__ %s\\r\\n" % rc)
    sys.stdout.flush()
    """
)

_KITTY_KEYS_OFF = "\x1b[=0;1u"
_MODIFY_OTHER_KEYS_OFF = "\x1b[>4;0m"


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


def _type(proc, data: bytes) -> str:
    try:
        proc.write(data)
    except OSError as exc:  # the driver is already gone; the assertions say why
        return f"<typing {data!r} failed: {exc}>"
    return ""


@pytest.mark.integration
def test_removing_the_session_dir_neither_ends_the_session_nor_strands_the_terminal(
    tmp_path,
):
    from ptyprocess import PtyProcess

    from _helpers.env import checkout_pythonpath

    surface = tmp_path / "fake_surface.py"
    surface.write_text(FAKE_SURFACE_SOURCE)
    session_dir = tmp_path / "project" / ".agent" / "run"
    driver = tmp_path / "driver.py"
    driver.write_text(
        DRIVER_SOURCE.format(
            src_roots=checkout_pythonpath(_REPO_ROOT).split(os.pathsep),
            session_dir=str(session_dir),
            surface=str(surface),
        )
    )
    # A short shutdown budget keeps the pre-fix run (relay broken, surface
    # killed) from spending the default grace.
    env = {**os.environ, "AI_HATS_PTY_GRACE_S": "0.5", "AI_HATS_PTY_TERM_S": "0.5"}

    proc = PtyProcess.spawn([sys.executable, str(driver)], dimensions=(24, 80), env=env)
    try:
        before = _read_until(proc.fd, "fake-surface ready", seconds=15.0)
        during = _type(proc, b"rm\r") + _read_until(proc.fd, "after-rm", seconds=5.0)
        after = _type(proc, b"quit\r") + _read_until(proc.fd, "__DRIVER_EXIT__", seconds=15.0)
        after += _read_until(proc.fd, "\n", seconds=1.0)
    finally:
        if proc.isalive():
            try:
                proc.kill(signal.SIGKILL)
            except Exception:  # noqa: S110 — teardown; the zombie drain below follows
                pass
        while True:
            try:
                reaped, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped == 0:
                break

    transcript = before + during + after
    assert not session_dir.exists(), "precondition: the surface removed the session dir"
    assert "__DRIVER_EXIT__ 0" in transcript, (
        f"the session did not survive its run dir; tail:\n{transcript[-600:]!r}"
    )
    assert "after-rm" in transcript, "the relay stopped relaying output after the rm"
    assert "got: quit" in transcript, "the keyboard went dead after the rm"
    assert transcript.count("session trace stopped mid-session") == 1

    # Positive control: the start-of-session reset is visible to this search.
    assert _KITTY_KEYS_OFF in before
    tail = transcript.split("bye", 1)[-1]
    assert _KITTY_KEYS_OFF in tail, f"kitty keyboard left on at exit: {tail!r}"
    assert _MODIFY_OTHER_KEYS_OFF in tail, f"modifyOtherKeys left on at exit: {tail!r}"
