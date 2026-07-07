"""Concurrency tests for TaskManager id allocation (HATS-936).

Root class of HATS-604: ``next_id()`` (read-max) + ``create_task`` (check-then-
write) were unserialised, so two concurrent processes could allocate the SAME
id and the second ``_save_task`` clobbered the first — silent task/plan loss.

These tests pin the fix: N processes that hit ``create_task(None)`` together
(``Barrier`` synchronised) must get N DISTINCT ids and N intact cards, with no
cross-written title. Revert the alloc lock and the distinct-id / title
assertion goes RED.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from ai_hats_tracker.state import TaskManager
from ai_hats.tracker_wiring import tracker_paths


pytestmark = pytest.mark.integration


def _make_project(tmp_path: Path) -> Path:
    """Bare ai-hats tracker project (mirrors the test_state.py ``mgr`` setup)."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    return project


def _manager(project: Path) -> TaskManager:
    # No worktree effects: create_task never touches them.
    return TaskManager(project, layout=tracker_paths(project), strict_plan_check=False)


def _create_worker(project_dir: str, marker: int, results: dict, barrier) -> None:
    """Child process: sync on the barrier, then allocate one card.

    Top-level for multiprocessing (spawn) pickling. Records the allocated id +
    the title it asked for, so the parent can detect both id collisions and
    cross-written titles.
    """
    project = Path(project_dir)
    mgr = _manager(project)
    title = f"proc-{marker}"
    try:
        barrier.wait(timeout=15)
        card, _ = mgr.create_task(None, title)
        results[marker] = {"id": card.id, "title": title, "error": None}
    except Exception as exc:  # noqa: BLE001 — recorded, asserted in parent
        results[marker] = {"id": None, "title": title, "error": f"{type(exc).__name__}: {exc}"}


def test_parallel_create_allocates_distinct_ids(tmp_path: Path) -> None:
    """N barrier-synced ``create_task(None)`` → N distinct ids, no clobber.

    Without the alloc lock two racers read the same ``next_id()`` before either
    writes, so ``set(ids)`` collapses below N (and one title is lost). Under the
    lock the allocation is a serialised allocate+reserve → every id is unique.
    """
    n = 5
    project = _make_project(tmp_path)

    manager = multiprocessing.Manager()
    results = manager.dict()
    barrier = manager.Barrier(n)

    procs = [
        multiprocessing.Process(target=_create_worker, args=(str(project), i, results, barrier))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    outcomes = [dict(results[i]) for i in range(n)]
    errors = [o["error"] for o in outcomes if o["error"] is not None]
    assert errors == [], f"no create should fail under the alloc lock: {errors}"

    ids = [o["id"] for o in outcomes]
    assert len(set(ids)) == n, f"id collision — allocation raced: {sorted(ids)}"

    # No cross-write: every reported (id, title) survives verbatim on disk.
    mgr = _manager(project)
    for o in outcomes:
        card = mgr.get_task(o["id"])
        assert card is not None, f"card {o['id']} missing on disk"
        assert card.title == o["title"], (
            f"card {o['id']} title clobbered: on-disk {card.title!r} != creator {o['title']!r}"
        )
