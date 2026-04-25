"""CLI tests for `ai-hats task transition` — focused on HATS-168 cancelled flow.

Manager-level coverage for cancelled lives in test_state.py; this file
exercises the click validation layer that enforces --resolution at the edge.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.models import TaskState
from ai_hats.state import TaskManager


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Bare project layout that `_task_manager` / `_project_dir` accept."""
    (tmp_path / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (tmp_path / ".agent" / "STATE.md").write_text("")
    return tmp_path


@pytest.fixture
def cli(monkeypatch, project_dir):
    monkeypatch.chdir(project_dir)
    return CliRunner()


def _seed_task(project_dir: Path, task_id: str = "T-1") -> None:
    TaskManager(project_dir, prefix="T").create_task(task_id, "Sample task")


def test_transition_cancelled_requires_resolution(cli, project_dir):
    """Bare `transition T-1 cancelled` (no --resolution) must fail loudly."""
    _seed_task(project_dir)

    result = cli.invoke(main, ["task", "transition", "T-1", "cancelled"])

    assert result.exit_code == 1, result.output
    assert "resolution" in result.output.lower()

    # Card untouched — still in brainstorm.
    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.BRAINSTORM


def test_transition_cancelled_with_resolution_succeeds(cli, project_dir):
    _seed_task(project_dir)

    result = cli.invoke(
        main,
        ["task", "transition", "T-1", "cancelled", "--resolution", "duplicate of T-99"],
    )
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.CANCELLED
    assert t.resolution == "duplicate of T-99"
    assert t.completed_at != ""


def test_transition_cancelled_blank_resolution_rejected(cli, project_dir):
    """Empty/whitespace resolution counts as missing — protects against
    `--resolution ""` slipping through as 'set but empty'."""
    _seed_task(project_dir)

    result = cli.invoke(
        main, ["task", "transition", "T-1", "cancelled", "--resolution", "   "]
    )
    assert result.exit_code == 1, result.output
    assert "resolution" in result.output.lower()


def test_transition_to_other_states_does_not_require_resolution(cli, project_dir):
    """Sanity: --resolution validation must not leak into normal transitions."""
    _seed_task(project_dir)

    result = cli.invoke(main, ["task", "transition", "T-1", "plan"])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.PLAN


def test_task_list_hides_cancelled_by_default(cli, project_dir):
    """`task list` (default) hides cancelled the same way it hides done/failed."""
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "Active task")
    mgr.create_task("T-2", "Closed task")
    mgr.transition("T-2", TaskState.CANCELLED, resolution="dup")

    result = cli.invoke(main, ["task", "list"])
    assert result.exit_code == 0, result.output
    assert "T-1" in result.output
    assert "T-2" not in result.output

    result_all = cli.invoke(main, ["task", "list", "--all"])
    assert "T-1" in result_all.output
    assert "T-2" in result_all.output
