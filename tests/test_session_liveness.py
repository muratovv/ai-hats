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
    assert data["start_time_utc"]


def test_anchor_unwritable_dir_reports_and_returns_none(tmp_path, caplog):
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x")
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        assert session_liveness.write_session_anchor(blocked) is None
    assert "anchor not written" in caplog.text


# ---------- record_surface_child ----------


def test_the_surface_child_joins_the_wrapper_without_displacing_it(tmp_path, live_proc):
    """Two owners after the second write, wrapper still first, both with baselines."""
    session_liveness.write_session_anchor(tmp_path)
    assert session_liveness.record_surface_child(tmp_path, live_proc.pid)

    (root, child) = session_liveness.session_owners(tmp_path)
    assert root[0] == os.getpid()
    assert root[1], "the wrapper's baseline must survive the second write"
    assert child[0] == live_proc.pid
    assert child[1], "a child with no baseline could be pinned by pid reuse"


@pytest.mark.parametrize("body", [None, "{ not json", "[]"])
def test_an_unusable_anchor_reports_rather_than_inventing_one(tmp_path, caplog, body):
    """No anchor means the claim never landed; writing one here would name a
    surface child as the sole owner of a dir with no recorded wrapper."""
    if body is not None:
        (tmp_path / ANCHOR_NAME).write_text(body)
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        assert session_liveness.record_surface_child(tmp_path, 4242) is None
    assert "surface child 4242 not recorded" in caplog.text


# ---------- LivenessSnapshot ----------


def test_live_owner_survives(tmp_path, live_proc):
    session_liveness.write_session_anchor(tmp_path)
    ((pid, start_time),) = session_liveness.session_owners(tmp_path)
    assert LivenessSnapshot.capture().is_live(pid, start_time) is True
    assert LivenessSnapshot.capture().is_live(live_proc.pid, None) is True


def test_dead_owner_is_certainly_dead():
    assert LivenessSnapshot.capture().is_live(_dead_pid(), None) is False


def test_reused_pid_is_dead(live_proc):
    stale = "Wed Jan  1 00:00:00 2000"
    assert LivenessSnapshot.capture().is_live(live_proc.pid, stale) is False


def test_the_pid_and_state_columns_are_stripped_and_padding_normalized():
    snapshot = LivenessSnapshot.capture(command=["echo", "  123 Ss+  Wed Jun  9 18:02:29 2026"])
    assert snapshot.start_times == {123: "Wed Jun 9 18:02:29 2026"}
    assert snapshot.zombies == frozenset()
    assert snapshot.is_live(123, "Wed Jun  9 18:02:29 2026") is True


def test_a_row_with_no_lstart_column_keeps_the_dir():
    """The pid is IN the table, so it exists — there is just nothing to compare.

    Comparing the stored ``""`` against a real baseline answers "reused" and
    reaps a live session; every other unknown in this module keeps the dir.
    """
    snapshot = LivenessSnapshot.capture(command=["echo", "123 Ss"])
    assert snapshot.start_times == {123: ""}
    assert snapshot.is_live(123, "Wed Jun  9 18:02:29 2026") is True
    assert snapshot.is_live(123, None) is True


def test_a_zombie_row_is_read_as_death_even_when_its_baseline_matches():
    """The row is otherwise perfect: same pid, the ``lstart`` it was born with.

    Only the state column parts a zombie from its living self — which is the
    whole reason the batched read now asks for one (HATS-1339 D3).
    """
    born = "Wed Jun  9 18:02:29 2026"
    snapshot = LivenessSnapshot.capture(command=["echo", f"123 Z+ {born}"])
    assert snapshot.zombies == frozenset({123})
    assert snapshot.is_live(123, born) is False
    assert snapshot.is_live(123, None) is False


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


# ---------- session_owners ----------


def test_anchor_wins_over_the_dir_name(tmp_path, live_proc):
    session_dir = tmp_path / "20260812-104832-1-999999"
    session_dir.mkdir()
    session_liveness.write_session_anchor(session_dir)
    ((pid, start_time),) = session_liveness.session_owners(session_dir)
    assert pid == os.getpid()
    assert start_time


def test_no_anchor_falls_back_to_the_dir_name(tmp_path):
    session_dir = tmp_path / "20260812-104832-1-58580"
    session_dir.mkdir()
    assert session_liveness.session_owners(session_dir) == ((58580, None),)


def test_nested_subagent_id_yields_the_child_pid(tmp_path):
    session_dir = tmp_path / "20260812-104832-1-58580_20260812-110000-2-58600"
    session_dir.mkdir()
    assert session_liveness.session_owners(session_dir) == ((58600, None),)


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
    assert session_liveness.session_owners(session_dir) == ()


@pytest.mark.parametrize(
    "body",
    [
        "{ not json",
        '{"root_pid": "58580"}',
        "[]",
        # isinstance(True, int) is True, so this used to resolve to pid 1
        # (launchd, never exits) and pin the dir forever.
        '{"root_pid": true}',
        '{"root_pid": 99999999999}',
    ],
)
def test_an_unusable_anchor_is_unresolvable_never_the_dir_name_pid(
    tmp_path, caplog, live_proc, body
):
    """``None`` — and emphatically not a fallback to the pid in the dir name.

    That pid is structurally the WRAPPER, so degrading to it silently drops a
    recorded surface child, the one owner that outlives a SIGKILLed wrapper on
    the sub-agent path. ``None`` tells the caller to keep the dir instead.
    """
    session_dir = tmp_path / f"20260812-104832-1-{live_proc.pid}"
    session_dir.mkdir()
    (session_dir / ANCHOR_NAME).write_text(body)
    with caplog.at_level(logging.WARNING, logger=session_liveness.__name__):
        assert session_liveness.session_owners(session_dir) is None
    assert "anchor" in caplog.text


@pytest.mark.parametrize("tail", ["99999999999", "0"])
def test_an_impossible_pid_in_the_dir_name_is_no_owner(tmp_path, tail):
    """A digit run past a C ``int`` reached ``os.kill`` as an uncaught
    ``OverflowError`` and broke every session start in the project."""
    assert session_liveness.session_owners(tmp_path / f"20260812-104832-1-{tail}") == ()


def test_a_legacy_start_time_is_not_read_as_a_baseline(tmp_path, live_proc):
    """Anchors already on disk carry a rendering from their writer's own TZ and
    locale; comparing one manufactures the very reuse verdict the rename avoids."""
    (tmp_path / ANCHOR_NAME).write_text(
        json.dumps({"root_pid": live_proc.pid, "start_time": "Mon Jan  1 00:00:00 2001"})
    )
    ((pid, start_time),) = session_liveness.session_owners(tmp_path)
    assert (pid, start_time) == (live_proc.pid, None)
    assert LivenessSnapshot.capture().is_live(pid, start_time) is True


# ---------- the baseline must not move with the ambient environment ----------


def test_the_recorded_baseline_ignores_the_ambient_timezone(monkeypatch, live_proc):
    """``ps -o lstart=`` renders in the TZ/locale of the ``ps`` PROCESS."""
    monkeypatch.setenv("TZ", "America/New_York")
    east = session_liveness._proc_start_time(live_proc.pid)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    west = session_liveness._proc_start_time(live_proc.pid)
    assert east == west
    assert east[1], "a live process must yield a baseline"


def test_an_anchor_written_under_another_timezone_still_reads_live(monkeypatch, tmp_path):
    """One session writes the baseline and ANOTHER compares it. When the two
    disagreed about the rendering, the mismatch was indistinguishable from pid
    reuse — and reaped a live session's cache with no TTL to slow it down."""
    monkeypatch.setenv("TZ", "America/New_York")
    session_liveness.write_session_anchor(tmp_path)

    monkeypatch.setenv("TZ", "Asia/Tokyo")
    owners = session_liveness.session_owners(tmp_path)
    assert owners is not None
    ((pid, start_time),) = owners
    assert start_time
    assert session_liveness.LazyLiveness().is_live(pid, start_time) is True


def test_a_pid_absent_from_a_stale_snapshot_is_not_a_death(live_proc):
    """``os.kill`` already proved the pid exists, so absence from the cached
    table dates the TABLE — one snapshot serves both sweeps."""
    empty = LivenessSnapshot(start_times={1: "x"}, available=True)
    lazy = session_liveness.LazyLiveness(capture=lambda: empty)
    assert lazy.is_live(live_proc.pid, "Wed Jan  1 00:00:00 2000") is True
