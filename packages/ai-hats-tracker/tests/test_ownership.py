"""Unit tests for the task-ownership registry (HATS-955).

Liveness is injected as a fake (the ``live`` fixture) so no real ``ps`` runs; the
real ``record_is_live`` gets one focused test against live/dead pids at the end.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_tracker import ownership
from ai_hats_tracker.ownership import OwnershipRefused


class _Liveness:
    """A fake liveness predicate: a record is live iff its root_pid was added."""

    def __init__(self) -> None:
        self.pids: set[int] = set()

    def add(self, *pids: int) -> None:
        self.pids.update(pids)

    def kill(self, pid: int) -> None:
        self.pids.discard(pid)

    def __call__(self, record: dict) -> bool:
        return record.get("root_pid") in self.pids


@pytest.fixture
def live() -> _Liveness:
    return _Liveness()


@pytest.fixture
def reg(tmp_path: Path) -> Path:
    return tmp_path / "ownership.json"


def test_take_on_empty_claims(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    rec = ownership.owner_of(reg, "T-1", is_live=live)
    assert rec is not None
    assert rec["session_id"] == "sess-a"
    assert rec["is_live"] is True


def test_owner_of_unowned_is_none(reg: Path, live: _Liveness) -> None:
    assert ownership.owner_of(reg, "T-404", is_live=live) is None


def test_reclaim_refused_when_live_other_owns(reg: Path, live: _Liveness) -> None:
    live.add(100, 200)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    with pytest.raises(OwnershipRefused) as ei:
        ownership.take(reg, "T-1", "sess-b", 200, is_live=live)
    assert ei.value.holder == "sess-a"
    assert ei.value.task_id == "T-1"


def test_reclaim_succeeds_when_owner_dead(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    live.kill(100)  # agent A dies
    live.add(200)
    ownership.take(reg, "T-1", "sess-b", 200, is_live=live)  # B reclaims
    rec = ownership.owner_of(reg, "T-1", is_live=live)
    assert rec["session_id"] == "sess-b"


def test_single_slot_refuses_second_task(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    with pytest.raises(OwnershipRefused) as ei:
        ownership.take(reg, "T-2", "sess-a", 100, is_live=live)
    assert "T-1" in ei.value.reason


def test_reexecute_same_task_is_idempotent(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)  # no raise
    assert ownership.held_by(reg, "sess-a") == ["T-1"]


def test_release_drops_own_only(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.release(reg, "T-1", "sess-other")  # not mine → no-op
    assert ownership.owner_of(reg, "T-1", is_live=live) is not None
    ownership.release(reg, "T-1", "sess-a")
    assert ownership.owner_of(reg, "T-1", is_live=live) is None


def test_release_after_stop_allows_reclaim(reg: Path, live: _Liveness) -> None:
    live.add(100, 200)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.release(reg, "T-1", "sess-a")  # stop
    ownership.take(reg, "T-1", "sess-b", 200, is_live=live)  # succeeds
    assert ownership.owner_of(reg, "T-1", is_live=live)["session_id"] == "sess-b"


def test_finish_drops_unconditionally(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.finish(reg, "T-1")
    assert ownership.owner_of(reg, "T-1", is_live=live) is None


def test_release_and_finish_noop_without_file(reg: Path) -> None:
    ownership.release(reg, "T-1", "sess-a")  # no file → no raise
    ownership.finish(reg, "T-1")
    assert not reg.exists()


def test_held_by_lists_own_session(reg: Path, live: _Liveness) -> None:
    live.add(100, 200)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.take(reg, "T-9", "sess-b", 200, is_live=live)
    assert ownership.held_by(reg, "sess-a") == ["T-1"]
    assert ownership.held_by(reg, "sess-b") == ["T-9"]


def test_sweep_drops_dead_keeps_live(reg: Path, live: _Liveness) -> None:
    live.add(100, 200)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    ownership.take(reg, "T-2", "sess-b", 200, is_live=live)
    live.kill(100)  # A dies
    removed = ownership.sweep(reg, is_live=live)
    assert removed == 1
    assert ownership.owner_of(reg, "T-1", is_live=live) is None
    assert ownership.owner_of(reg, "T-2", is_live=live) is not None


def test_corrupt_registry_reads_as_empty(reg: Path, live: _Liveness) -> None:
    reg.write_text("{ this is not json", encoding="utf-8")
    assert ownership.owner_of(reg, "T-1", is_live=live) is None
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)  # recovers
    assert ownership.owner_of(reg, "T-1", is_live=live) is not None


def test_owner_of_marks_dead_owner(reg: Path, live: _Liveness) -> None:
    live.add(100)
    ownership.take(reg, "T-1", "sess-a", 100, is_live=live)
    live.kill(100)
    rec = ownership.owner_of(reg, "T-1", is_live=live)
    assert rec is not None and rec["is_live"] is False


def test_record_is_live_real_pids() -> None:
    """The real (un-faked) liveness: this process is live; a reaped pid is dead."""
    alive = {"root_pid": os.getpid(), "start_time": ownership._capture_start_time(os.getpid())}
    assert ownership.record_is_live(alive) is True

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead = {"root_pid": proc.pid, "start_time": "Mon Jan  1 00:00:00 2001"}
    assert ownership.record_is_live(dead) is False

    assert ownership.record_is_live({"root_pid": None}) is False
    assert ownership.record_is_live({"root_pid": 0}) is False
