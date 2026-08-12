"""Session-owner liveness truth table (HATS-1339 / S1).

Every row the sweeps will gate on: alive, certainly dead, pid reused, ``ps``
unavailable, no anchor, a nested sub-agent id, and a malformed anchor. The
uncertainty rows are the point — each must keep the dir.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

import pytest

from ai_hats import session_liveness
from ai_hats.session_liveness import ANCHOR_NAME, LivenessSnapshot


@pytest.fixture
def live_proc():
    """A real, live child process whose pid we can probe; killed on teardown."""
    p = subprocess.Popen(["sleep", "60"])
    try:
        yield p
    finally:
        p.kill()
        p.wait()


def _dead_pid() -> int:
    """A pid that is definitely no longer running (spawned, terminated, reaped)."""
    p = subprocess.Popen(["sleep", "30"])
    p.terminate()
    p.wait()
    return p.pid


# ---------- write_session_anchor ----------


def test_anchor_records_this_process(tmp_path):
    written = session_liveness.write_session_anchor(tmp_path)
    assert written == tmp_path / ANCHOR_NAME
    data = json.loads(written.read_text())
    assert data["root_pid"] == os.getpid()
    assert data["start_time"]


def test_anchor_unwritable_dir_reports_and_returns_none(tmp_path, caplog):
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x")
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        assert session_liveness.write_session_anchor(blocked) is None
    assert "anchor not written" in caplog.text


# ---------- LivenessSnapshot ----------


def test_live_owner_survives(tmp_path, live_proc):
    session_liveness.write_session_anchor(tmp_path)
    pid, start_time = session_liveness.session_owner(tmp_path)
    assert LivenessSnapshot.capture().is_live(pid, start_time) is True
    assert LivenessSnapshot.capture().is_live(live_proc.pid, None) is True


def test_dead_owner_is_certainly_dead():
    assert LivenessSnapshot.capture().is_live(_dead_pid(), None) is False


def test_reused_pid_is_dead(live_proc):
    stale = "Wed Jan  1 00:00:00 2000"
    assert LivenessSnapshot.capture().is_live(live_proc.pid, stale) is False


def test_the_pid_column_is_stripped_and_padding_normalized():
    snapshot = LivenessSnapshot.capture(command=["echo", "  123 Wed Jun  9 18:02:29 2026"])
    assert snapshot.start_times == {123: "Wed Jun 9 18:02:29 2026"}
    assert snapshot.is_live(123, "Wed Jun  9 18:02:29 2026") is True


@pytest.mark.parametrize(
    ("command", "reported"),
    [
        (["ai-hats-no-such-ps-binary"], "process table unavailable"),
        (["false"], "ps exited"),
        (["true"], "no parseable rows"),
    ],
)
def test_unusable_ps_degrades_to_os_kill(live_proc, caplog, command, reported):
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        snapshot = LivenessSnapshot.capture(command=command)
    assert snapshot.available is False
    assert reported in caplog.text
    # os.kill cannot detect reuse, so even a mismatched baseline keeps the dir.
    assert snapshot.is_live(live_proc.pid, "Wed Jan  1 00:00:00 2000") is True
    assert snapshot.is_live(_dead_pid(), None) is False


def test_unresolved_owner_is_not_a_death(live_proc):
    assert LivenessSnapshot.capture().is_live(None, None) is True


# ---------- session_owner ----------


def test_anchor_wins_over_the_dir_name(tmp_path, live_proc):
    session_dir = tmp_path / "20260812-104832-1-999999"
    session_dir.mkdir()
    session_liveness.write_session_anchor(session_dir)
    pid, start_time = session_liveness.session_owner(session_dir)
    assert pid == os.getpid()
    assert start_time


def test_no_anchor_falls_back_to_the_dir_name(tmp_path):
    session_dir = tmp_path / "20260812-104832-1-58580"
    session_dir.mkdir()
    assert session_liveness.session_owner(session_dir) == (58580, None)


def test_nested_subagent_id_yields_the_child_pid(tmp_path):
    session_dir = tmp_path / "20260812-104832-1-58580_20260812-110000-2-58600"
    session_dir.mkdir()
    assert session_liveness.session_owner(session_dir) == (58600, None)


@pytest.mark.parametrize(
    "name",
    [
        "dry-run",
        "dry-run-materialize",
        "legacy_session",
        # Pre-HATS-1248 ids stop at the COUNTER. Reading that tail as a pid made
        # every one of them the property of pid 1 (launchd), which never exits.
        "20260529-084521-1",
        "sid-1",
    ],
)
def test_no_owner_anywhere_leaves_the_caller_on_ttl(tmp_path, name):
    session_dir = tmp_path / name
    session_dir.mkdir()
    assert session_liveness.session_owner(session_dir) == (None, None)


@pytest.mark.parametrize("body", ["{ not json", '{"root_pid": "58580"}', "[]"])
def test_malformed_anchor_reports_and_never_invents_a_death(tmp_path, caplog, live_proc, body):
    session_dir = tmp_path / f"20260812-104832-1-{live_proc.pid}"
    session_dir.mkdir()
    (session_dir / ANCHOR_NAME).write_text(body)
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        pid, start_time = session_liveness.session_owner(session_dir)
    assert "anchor" in caplog.text
    assert (pid, start_time) == (live_proc.pid, None)
    assert LivenessSnapshot.capture().is_live(pid, start_time) is True
