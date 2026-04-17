"""Concurrency tests for worktree state I/O (HATS-121).

Three race scenarios:

* TC-1 — write/write: two processes hammer ``save_state`` on the same
  key with different payloads; the on-disk JSON must always be valid
  and equal to one of the two payloads.
* TC-2 — read/clear: writer thrashes ``save_state`` + ``_clear_state``
  while a reader loops on ``_load_by_key``; the reader must see either
  a fully valid state or ``None``, never a partial/corrupt JSON.
* TC-3 — acquire timeout: holding the lock from one process makes a
  second acquire raise :class:`WorktreeLockError` once the timeout
  elapses.
"""

from __future__ import annotations

import json
import multiprocessing
import subprocess
import time
from pathlib import Path

import pytest

from ai_hats.worktree import (
    STATES_DIR,
    WorktreeLockError,
    WorktreeManager,
    _acquire,
    _state_key,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


# ---------------------------------------------------------------------------
# Worker functions (must be top-level for multiprocessing pickling)
# ---------------------------------------------------------------------------


def _writer_worker(project_dir: str, branch: str, marker: str, iterations: int) -> None:
    """Repeatedly save_state with a payload identifiable by ``marker``."""
    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch)
    mgr.worktree_path = project / f"fake-wt-{marker}"
    mgr._original_branch = "main"
    for _ in range(iterations):
        mgr.save_state()


def _read_clear_writer(project_dir: str, branch: str, iterations: int) -> None:
    """Cycle save_state + _clear_state to maximize read/clear contention."""
    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch)
    mgr.worktree_path = project / "fake-wt"
    mgr._original_branch = "main"
    for _ in range(iterations):
        mgr.save_state()
        mgr._clear_state()


def _read_loop(project_dir: str, branch: str, iterations: int, errors: list) -> None:
    """Load by key in a loop; record any unexpected exception."""
    project = Path(project_dir)
    key = _state_key(branch)
    for _ in range(iterations):
        try:
            WorktreeManager._load_by_key(project, key)
        except Exception as exc:  # pragma: no cover — should not happen
            errors.append(repr(exc))


def _hold_lock(state_path_str: str, hold_seconds: float, ready_path: str) -> None:
    """Acquire the lock for ``state_path`` and hold it for ``hold_seconds``."""
    state_path = Path(state_path_str)
    with _acquire(state_path, timeout=10.0):
        Path(ready_path).write_text("ready")
        time.sleep(hold_seconds)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_state_write_write_race(git_project: Path) -> None:
    """TC-1: parallel writers never corrupt the JSON."""
    branch = "task/hats-121-tc1"
    iterations = 25

    p1 = multiprocessing.Process(
        target=_writer_worker, args=(str(git_project), branch, "alpha", iterations)
    )
    p2 = multiprocessing.Process(
        target=_writer_worker, args=(str(git_project), branch, "beta", iterations)
    )
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)

    assert p1.exitcode == 0, "writer alpha failed"
    assert p2.exitcode == 0, "writer beta failed"

    state_path = git_project / STATES_DIR / f"{_state_key(branch)}.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())  # never raises
    assert data["branch"] == branch
    assert data["worktree_path"] in {
        str(git_project / "fake-wt-alpha"),
        str(git_project / "fake-wt-beta"),
    }


def test_load_during_clear_race(git_project: Path) -> None:
    """TC-2: reader sees only valid state or ``None``, never corruption."""
    branch = "task/hats-121-tc2"
    writer_iters = 60
    reader_iters = 200

    manager = multiprocessing.Manager()
    errors: list = manager.list()

    writer = multiprocessing.Process(
        target=_read_clear_writer, args=(str(git_project), branch, writer_iters)
    )
    reader = multiprocessing.Process(
        target=_read_loop, args=(str(git_project), branch, reader_iters, errors)
    )
    writer.start()
    reader.start()
    writer.join(timeout=30)
    reader.join(timeout=30)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    assert list(errors) == [], f"reader observed errors: {list(errors)}"


def test_acquire_timeout_raises_worktree_lock_error(
    git_project: Path, tmp_path: Path
) -> None:
    """TC-3: a stuck lock holder triggers WorktreeLockError on second acquire."""
    state_path = git_project / STATES_DIR / "task-hats-121-tc3.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ready = tmp_path / "ready.flag"

    holder = multiprocessing.Process(
        target=_hold_lock, args=(str(state_path), 5.0, str(ready))
    )
    holder.start()
    try:
        # Wait for the holder to actually grab the lock.
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), "holder did not acquire lock in time"

        with pytest.raises(WorktreeLockError) as ei:
            with _acquire(state_path, timeout=0.5):
                pass
        msg = str(ei.value)
        assert "locked by another process" in msg
        assert state_path.name in msg
    finally:
        holder.join(timeout=10)
        assert holder.exitcode == 0
