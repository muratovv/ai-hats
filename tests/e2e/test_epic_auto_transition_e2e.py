"""E2E gate for HATS-690: child-driven epic auto-transitions via the real CLI.

Runs the **real** ai-hats binary (pip-installed from the local repo via
``tmp_venv_project``) against a real git project. Exists specifically to
satisfy ``dev_rule_e2e_gate`` for the ``src/ai_hats/cli/task.py`` edit that
prints the auto-transition notice (``_print_auto_transitions``).

**Fail-under-revert check**: revert ``_propagate_to_parent`` (or its call sites)
in ``state.py`` → both ``expect_stdout_contains("Epic auto-transition", ...)``
assertions fail (no notice is printed and the epic never changes state).
Reviewer rejects if the test passes both with and without the propagation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _git_init_on_master(project: Path) -> None:
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    (project / ".agent").mkdir(exist_ok=True)
    (project / ".agent" / "STATE.md").write_text("")
    (project / ".agent" / "backlog").mkdir(parents=True, exist_ok=True)
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def _plan_path(project: Path, task_id: str) -> Path:
    return (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )


def test_epic_auto_advance_and_reopen_e2e(tmp_venv_project) -> None:
    """Full chain through the real binary: advance to review, then reopen.

    1. ``git init -b master`` + commit + .agent layout.
    2. Create an epic (TASK-001) + 2 children (TASK-002 / TASK-003).
    3. Walk the epic to ``execute`` (fill its plan; real worktree).
    4. Fast-close both children → the second close auto-advances the epic to
       ``review`` and prints the notice.
    5. Reviewer closes the epic to ``done``.
    6. ``task create --parent-task TASK-001`` a 3rd child → the epic auto-reopens
       to ``execute`` and prints the notice.
    """
    proj = tmp_venv_project
    project = proj.path
    _git_init_on_master(project)

    proj.run("task", "create", "Epic").expect_ok()  # TASK-001
    proj.run("task", "create", "Child 1", "--parent-task", "TASK-001").expect_ok()
    proj.run("task", "create", "Child 2", "--parent-task", "TASK-001").expect_ok()

    # Walk the epic to execute (real worktree). Fill the scaffold so the
    # per-section gate passes (HATS-635).
    proj.run("task", "transition", "TASK-001", "plan").expect_ok()
    plan_path = _plan_path(project, "TASK-001")
    assert plan_path.exists(), f"expected plan scaffold at {plan_path}"
    plan_path.write_text(
        "# Plan\n\n"
        "## Requirements\nepic probe\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    proj.run("task", "transition", "TASK-001", "execute").expect_ok()

    # Fast-close both children; the SECOND completes the epic → auto-advance.
    proj.run("task", "close", "TASK-002", "--resolution", "shipped").expect_ok()
    (
        proj.run("task", "close", "TASK-003", "--resolution", "shipped")
        .expect_ok()
        .expect_stdout_contains("Epic auto-transition", "TASK-001", "review")
    )
    show = proj.run("task", "show", "TASK-001").expect_ok()
    assert "state: review" in show.stdout, (
        f"epic should be in review after all children resolved; got:\n"
        f"{show.stdout[-400:]}"
    )

    # Reviewer closes the epic.
    proj.run("task", "transition", "TASK-001", "done").expect_ok()

    # New work under the done epic → auto-reopen to execute.
    (
        proj.run("task", "create", "Child 3", "--parent-task", "TASK-001")
        .expect_ok()
        .expect_stdout_contains("Epic auto-transition", "TASK-001", "execute")
    )
    show = proj.run("task", "show", "TASK-001").expect_ok()
    assert "state: execute" in show.stdout, (
        f"epic should reopen to execute after new child; got:\n"
        f"{show.stdout[-400:]}"
    )
