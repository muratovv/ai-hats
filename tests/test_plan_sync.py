"""Tests for plan-sync — moves .claude/plans/<NN>-*.md → backlog plan.md."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.models import TaskState
from ai_hats.state import (
    EmptyPlanError,
    PLAN_SCAFFOLD,
    PlanSyncAmbiguousError,
    TaskManager,
)


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
    (p / ".claude" / "plans").mkdir(parents=True)
    return p


@pytest.fixture
def mgr(project: Path) -> TaskManager:
    return TaskManager(project, prefix="HATS")


def test_transition_plan_imports_single_match(mgr: TaskManager, project: Path) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
    src = project / ".claude" / "plans" / "230-mighty-acorn.md"
    src.write_text("# Real plan\n\nObjective: ship plan-sync.\n")

    mgr.transition("HATS-230", TaskState.PLAN)

    dst = mgr.tasks_dir / "HATS-230" / "plan.md"
    assert dst.read_text() == "# Real plan\n\nObjective: ship plan-sync.\n"
    assert not src.exists(), "source must be moved, not copied"


def test_transition_plan_no_match_keeps_scaffold(mgr: TaskManager) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
    mgr.transition("HATS-230", TaskState.PLAN)

    dst = mgr.tasks_dir / "HATS-230" / "plan.md"
    assert dst.read_text() == PLAN_SCAFFOLD.format(
        task_id="HATS-230", title="Test plan-sync"
    )


def test_transition_plan_ambiguous_match_raises(mgr: TaskManager, project: Path) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
    (project / ".claude" / "plans" / "230-foo.md").write_text("foo")
    (project / ".claude" / "plans" / "230-bar.md").write_text("bar")

    with pytest.raises(PlanSyncAmbiguousError) as exc:
        mgr.transition("HATS-230", TaskState.PLAN)
    assert exc.value.task_id == "HATS-230"
    assert len(exc.value.matches) == 2


def test_transition_plan_matches_prefix_form(mgr: TaskManager, project: Path) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
    src = project / ".claude" / "plans" / "hats-230-foo.md"
    src.write_text("# Prefixed plan\n")

    mgr.transition("HATS-230", TaskState.PLAN)

    dst = mgr.tasks_dir / "HATS-230" / "plan.md"
    assert dst.read_text() == "# Prefixed plan\n"
    assert not src.exists()


def test_transition_execute_blocks_on_empty_scaffold(mgr: TaskManager) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
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
    "# Plan for HATS-230: Test plan-sync\n\n"
    "## Requirements\nShip it.\n\n"
    "## Scope & Out-of-scope\nIn: gate. Out: skill.\n\n"
    "## Steps\n- [x] do thing\n\n"
    "## Verification Protocol\npytest -q\n"
)


def test_transition_execute_proceeds_on_populated_plan(
    mgr: TaskManager, project: Path
) -> None:
    mgr.create_task("HATS-230", "Test plan-sync")
    src = project / ".claude" / "plans" / "230-foo.md"
    src.write_text(_FILLED_PLAN)  # all required sections filled
    mgr.transition("HATS-230", TaskState.PLAN)

    # execute should not raise — every required section has content.
    t = mgr.transition("HATS-230", TaskState.EXECUTE)
    assert t.state == TaskState.EXECUTE


def test_find_claude_plan_for_task_returns_empty_when_dir_missing(
    tmp_path: Path,
) -> None:
    p = tmp_path / "no-claude"
    p.mkdir()
    (p / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    mgr = TaskManager(p, prefix="HATS")
    assert mgr.find_claude_plan_for_task("HATS-001") == []
