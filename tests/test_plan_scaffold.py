"""Tests for the plan scaffold + the transition→execute empty-plan gate.

The `.claude/plans → plan-sync` import path was removed in HATS-637: a plan is
always a task and always lives at the canonical `tasks/<ID>/plan.md`. What
remains is the scaffold written on transition→plan and the per-section gate
on transition→execute (HATS-635) — both covered here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.models import TaskState
from ai_hats_tracker.state import EmptyPlanError, PLAN_SCAFFOLD, TaskManager
from ai_hats.tracker_wiring import tracker_paths


pytestmark = pytest.mark.integration


def _init_git(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project,
        check=True,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    _init_git(p)
    (p / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (p / ".agent" / "STATE.md").write_text("")
    return p


@pytest.fixture
def mgr(project: Path) -> TaskManager:
    return TaskManager(project, prefix="HATS", layout=tracker_paths(project))


def test_transition_plan_writes_scaffold(mgr: TaskManager) -> None:
    mgr.create_task("HATS-230", "Test scaffold")
    mgr.transition("HATS-230", TaskState.PLAN)

    dst = mgr.tasks_dir / "HATS-230" / "plan.md"
    assert dst.read_text() == PLAN_SCAFFOLD.format(
        task_id="HATS-230", title="Test scaffold"
    )


def test_transition_execute_blocks_on_empty_scaffold(mgr: TaskManager) -> None:
    mgr.create_task("HATS-230", "Test scaffold")
    mgr.transition("HATS-230", TaskState.PLAN)  # scaffold only

    with pytest.raises(EmptyPlanError) as exc:
        mgr.transition("HATS-230", TaskState.EXECUTE)
    assert exc.value.task_id == "HATS-230"
    # The error must NAME every empty required section (HATS-635).
    assert exc.value.empty_sections == [
        "Requirements",
        "Scope & Out-of-scope",
        "Steps",
        "Verification Protocol",
    ]


_FILLED_PLAN = (
    "# Plan for HATS-230: Test scaffold\n\n"
    "## Requirements\nShip it.\n\n"
    "## Scope & Out-of-scope\nIn: gate. Out: skill.\n\n"
    "## Steps\n- [x] do thing\n\n"
    "## Verification Protocol\npytest -q\n"
)


def test_transition_execute_proceeds_on_populated_plan(mgr: TaskManager) -> None:
    mgr.create_task("HATS-230", "Test scaffold")
    mgr.transition("HATS-230", TaskState.PLAN)
    # Plan content goes straight into the canonical location — no .claude/plans.
    (mgr.tasks_dir / "HATS-230" / "plan.md").write_text(_FILLED_PLAN)

    # execute should not raise — every required section has content.
    t, _ = mgr.transition("HATS-230", TaskState.EXECUTE)
    assert t.state == TaskState.EXECUTE
