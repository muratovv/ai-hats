"""ADR-0014 Phase 2 (T16a / HATS-933) — standalone consumability for the tracker.

Proves a third party can ``from ai_hats_tracker import TaskManager`` and drive
the full ``brainstorm → … → done`` task FSM — create, transition, close, link,
log, STATE.md sync — on a bare directory with ``worktree_effects=None``: no wt,
no ``ai-hats.yaml``, no composition. Imports ONLY the ``ai_hats_tracker`` public
surface (``__init__.__all__``), never a submodule or an ai-hats accretion.
"""

from __future__ import annotations

from pathlib import Path

import ai_hats_tracker as tracker
from ai_hats_tracker import TaskManager, TaskState, TrackerPaths

# The minimal public surface a standalone consumer needs to drive the FSM.
_STANDALONE_SURFACE = {"TaskCard", "TaskState", "TaskManager", "TrackerPaths", "WorktreeEffects"}


def _layout(project: Path) -> TrackerPaths:
    """A project-local layout with no ``ai_hats.paths`` — ``ensure_base=None``
    makes the manager mkdir its own injected dirs."""
    agent = project / ".agent"
    return TrackerPaths(
        tasks_dir=agent / "tasks",
        state_md_path=agent / "STATE.md",
        legacy_backlog_md=agent / "BACKLOG.md",
        ensure_base=None,
    )


def test_public_surface_is_sufficient() -> None:
    """The standalone-consumer surface is exported from ``__all__``.

    RED-under-revert: dropping any standalone name from ``__init__.__all__``
    fails this assertion.
    """
    assert _STANDALONE_SURFACE <= set(tracker.__all__), (
        f"ai_hats_tracker.__all__ must export the standalone surface "
        f"{sorted(_STANDALONE_SURFACE)}; missing "
        f"{sorted(_STANDALONE_SURFACE - set(tracker.__all__))}"
    )


def test_drive_full_fsm_on_bare_dir(tmp_path: Path) -> None:
    """create → log → transition brainstorm→…→done, with STATE.md synced, and
    zero ai-hats project config (no ``ai-hats.yaml``, ``worktree_effects=None``)."""
    assert not (tmp_path / "ai-hats.yaml").exists()
    layout = _layout(tmp_path)
    mgr = TaskManager(tmp_path, layout=layout, strict_plan_check=False, worktree_effects=None)

    task_id = mgr.next_id()
    card, _ = mgr.create_task(task_id, "Standalone probe")
    assert card.state == TaskState.BRAINSTORM

    mgr.log_work(task_id, "started under a bare consumer")
    for state in (
        TaskState.PLAN,
        TaskState.EXECUTE,
        TaskState.DOCUMENT,
        TaskState.REVIEW,
        TaskState.DONE,
    ):
        mgr.transition(task_id, state)

    saved = mgr.get_task(task_id)
    assert saved is not None and saved.state == TaskState.DONE
    assert (layout.tasks_dir / task_id / "task.yaml").exists()

    # STATE.md index synced with the terminal state — no ai-hats config needed.
    assert layout.state_md_path.exists()
    assert task_id in layout.state_md_path.read_text()


def test_close_link_and_sync_on_bare_dir(tmp_path: Path) -> None:
    """close_task / add_link / sync round out the DoD surface, still wt-free."""
    layout = _layout(tmp_path)
    mgr = TaskManager(tmp_path, layout=layout, worktree_effects=None)

    parent = mgr.next_id()
    mgr.create_task(parent, "Parent")
    child = mgr.next_id()
    mgr.create_task(child, "Child")

    mgr.add_link(parent, child, "related")
    assert child in mgr.get_task(parent).related

    closed, _ = mgr.close_task(parent, resolution="shipped out-of-band")
    assert closed.state == TaskState.DONE

    assert mgr.sync() == 2
