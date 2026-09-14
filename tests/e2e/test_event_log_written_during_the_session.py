"""e2e (HATS-1967)

flow:   an operator runs an interactive session; while the surface is still
        running, the session's events.jsonl already holds what the surface
        has done so far, and when the surface exits the file is complete
        without ever having been rewritten
cmds:
    # the real PTY spawn with a stand-in surface that writes its transcript
    # record by record, then waits for input
    ai-hats -r assistant
    tail -f <session_dir>/events.jsonl   # in a second terminal
expect: events.jsonl carries the first response while the surface child is
        alive; after the child exits the file equals a finished-record read of
        the transcript, on the same inode it was created on
why:    the live writer runs on a thread beside a real pty child and is closed
        from the finalize chain — an in-process test drives its tick by hand
        and cannot see whether the thread follows a record a real child is
        appending to, nor whether the close lands after the child is reaped
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import select
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ai_hats_observe.canonical import ResponseStarted
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

pytestmark = [pytest.mark.integration, pytest.mark.observe, pytest.mark.surfaces]

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    _REPO_ROOT
    / "packages"
    / "ai-hats-observe"
    / "tests"
    / "fixtures"
    / "transcripts"
    / "fragments.jsonl"
)

# Stand-in surface: appends its transcript one record every 300 ms — the shape
# of a real surface mid-turn — then waits on stdin so the test can look at the
# session's record while the child is alive, and exits on the first line.
FAKE_PROVIDER_SOURCE = textwrap.dedent(
    """\
    import sys, time
    records = open(sys.argv[2], encoding="utf-8").read().splitlines(keepends=True)
    with open(sys.argv[1], "a", encoding="utf-8") as transcript:
        for line in records:
            transcript.write(line)
            transcript.flush()
            time.sleep(0.3)
    sys.stdout.write("fake-claude ready> ")
    sys.stdout.flush()
    sys.stdin.readline()
    """
)

# Driver: the runner's own wiring — start the writer, run the REAL _pty_spawn,
# close the writer from the finalize step — against the stand-in surface.
# Inserts the worktree srcs FIRST so an editable install pointing at another
# checkout does not shadow this branch (HATS-863 precedent).
DRIVER_SOURCE = textwrap.dedent(
    """\
    import sys
    sys.path[:0] = {src_roots!r}
    from pathlib import Path
    from ai_hats.runtime import WrapRunner
    from ai_hats.runtime_common import _finalize_session_basic, start_event_log
    from ai_hats.surfaces.claude.provider import ClaudeSurface
    from ai_hats_observe import Session, SidecarTracer

    session_dir = Path({session_dir!r})
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session("e2e-live", session_dir)
    session.init_audit(role="assistant", provider="claude")
    transcript = Path({transcript!r})

    class StandIn(ClaudeSurface):
        def resolve_transcript(self, project_dir, session_id, *, provider_session_id=None, end_ts=None):
            return [transcript] if transcript.exists() else []

    writer = start_event_log(
        StandIn(), session, project_dir=session_dir.parent, provider_session_id="stand-in"
    )
    runner = WrapRunner.__new__(WrapRunner)
    rc = WrapRunner._pty_spawn(
        runner,
        [sys.executable, {provider!r}, str(transcript), {fixture!r}],
        {{}},
        SidecarTracer(session),
    )
    _finalize_session_basic(
        session, exit_code=rc, active_role="assistant", provider_name="claude", event_log=writer
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
            ready, _, _ = select.select([fd], [], [], 0.1)
        except OSError:
            break
        if ready:
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
        out += _drain(fd, 0.2)
    return out


def _wait_for_first_response(log: Path, seconds: float) -> list:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        events = list(read_events(log)) if log.exists() else []
        if any(isinstance(e, ResponseStarted) for e in events):
            return events
        time.sleep(0.05)
    return []


def test_the_event_log_is_written_while_the_surface_runs(tmp_path: Path) -> None:
    """Positive control for the assertion that matters: with ``start_event_log``
    stubbed to return ``None`` the first block below fails (no file while the
    child is alive), so a green run proves the thread followed a live record."""
    from ptyprocess import PtyProcess

    from _helpers.env import checkout_pythonpath

    transcript = tmp_path / "transcript.jsonl"
    session_dir = tmp_path / "sessions" / "session_e2e-live"
    log = session_dir / EVENT_LOG_JSONL
    provider = tmp_path / "fake_provider.py"
    provider.write_text(FAKE_PROVIDER_SOURCE, encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(
        DRIVER_SOURCE.format(
            src_roots=checkout_pythonpath(_REPO_ROOT).split(os.pathsep),
            session_dir=str(session_dir),
            transcript=str(transcript),
            provider=str(provider),
            fixture=str(FIXTURE),
        ),
        encoding="utf-8",
    )

    proc = PtyProcess.spawn([sys.executable, str(driver)], dimensions=(24, 80))
    try:
        # --- while the child is alive ------------------------------------
        during = _wait_for_first_response(log, seconds=20.0)
        assert during, f"no response in {log} while the surface was still running"
        assert proc.isalive(), "the surface exited before the record was looked at"
        seen_so_far = _drain(proc.fd, 0.2)
        assert "__DRIVER_EXIT__" not in seen_so_far
        inode_during = log.stat().st_ino

        # --- let the surface exit ----------------------------------------
        proc.write(b"\r")
        transcript_out = _read_until(proc.fd, "__DRIVER_EXIT__", seconds=20.0)
        assert "__DRIVER_EXIT__ 0" in transcript_out, transcript_out[-600:]
    finally:
        if proc.isalive():
            proc.terminate(force=True)

    # --- after: complete, on the same inode ----------------------------------
    after = list(read_events(log))
    assert after == list(ClaudeTranscriptReader(transcript).read())
    assert len(after) > len(during) or after == during
    assert after[: len(during)] == during, "the live prefix was rewritten at close"
    assert log.stat().st_ino == inode_during, "the file was replaced, not appended"
    assert "events.jsonl:" in (session_dir / "trace.log").read_text(encoding="utf-8")
