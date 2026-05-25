"""E2E gate for HATS-518: ``ai-hats wt create`` refuses when HEAD ≠ master.

Runs the **real** ai-hats binary (pip-installed from the local repo via
``tmp_venv_project``) against a real git project. Mirrors the canonical
e2e pattern from ``test_install.py`` — exists specifically to satisfy
``dev_rule_e2e_gate`` for changes in ``src/ai_hats/cli/worktree.py``
and ``src/ai_hats/cli/task.py``.

**Fail-under-revert check**: revert the guard from ``cli/worktree.py``
(or remove ``_assert_head_is_canonical_base`` from ``worktree.py``) →
``test_wt_create_refuses_on_feature_branch`` must fail with
``expected non-zero exit, got 0``. Reviewer rejects if the test passes
both with and without the guard.
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
    """git init -b master + one commit + .agent layout for _project_dir()."""
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    (project / ".agent").mkdir(exist_ok=True)
    (project / ".agent" / "STATE.md").write_text("")
    (project / ".agent" / "backlog").mkdir(parents=True, exist_ok=True)
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def test_wt_create_refuses_on_feature_branch(tmp_venv_project) -> None:
    """Full e2e: real binary on master vs feature branch.

    1. ``git init -b master`` + commit + .agent layout.
    2. ``git checkout -b feat/parking``.
    3. ``ai-hats wt create task/probe`` → exit 1, red "Refused", message
       names both ``feat/parking`` and ``master``.
    4. ``git worktree list`` shows only the main worktree (no leak).
    5. ``git checkout master``.
    6. ``ai-hats wt create task/probe`` → exit 0, ``Worktree created``,
       ``git worktree list`` now shows two entries.
    """
    proj = tmp_venv_project
    project = proj.path

    _git_init_on_master(project)
    _git(project, "checkout", "-b", "feat/parking")

    # --- Refusal path ---
    result = proj.run("wt", "create", "task/probe")
    (
        result
        .expect_failure()
        .expect_stdout_contains("Refused", "feat/parking", "master")
    )

    # No leaked worktree directory: only the main repo's worktree should
    # be listed. `git worktree list` first line is the main worktree.
    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 1, (
        f"Refusal should not create a worktree; got {len(wt_list)} entries:\n"
        + "\n".join(wt_list)
    )

    # --- Happy path (sanity: guard didn't break the normal flow) ---
    _git(project, "checkout", "master")
    result = proj.run("wt", "create", "task/probe")
    result.expect_ok().expect_stdout_contains("Worktree created", "task/probe")

    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 2, (
        f"Expected 2 worktrees after happy-path create; got {len(wt_list)}:\n"
        + "\n".join(wt_list)
    )

    # Cleanup the worktree we just created so we don't leak directories
    # outside tmp_path. `wt discard` exits with the worktree handler's
    # standard codes; tolerate either 0 or 2 (partial cleanup is fine in
    # tmp, the dir is under tmp_path and pytest will sweep it).
    proj.run("wt", "discard", "task/probe", "--force")


def test_task_transition_execute_refuses_on_feature_branch(tmp_venv_project) -> None:
    """E2E gate for the second call site (`cli/task.py`).

    Covers the path the unit test exercises via `TaskManager.transition()`
    directly — but here through the real binary, real backlog state file,
    and real disk I/O. Without this, a regression that breaks the
    `WorktreeBaseBranchError` propagation in `cli/task.py::task_transition`
    (e.g. accidentally catching it as a generic `Exception` earlier in
    the chain) would slip past the unit suite.

    1. ``git init -b master`` + commit + .agent layout.
    2. Seed a task and transition it to ``plan``.
    3. ``git checkout -b feat/parking``.
    4. ``ai-hats task transition <ID> execute`` → exit 1, stderr/stdout
       names ``Refused`` + the feature branch + ``master``.
    5. Task card on disk is still in ``plan`` state (no partial commit).
    """
    proj = tmp_venv_project
    project = proj.path

    _git_init_on_master(project)

    # Seed a card and walk it to PLAN via the real CLI. The project has no
    # `ai-hats.yaml` (tmp_venv_project skips init), so prefix falls back to
    # the default `TASK` per `ProjectConfig.resolve_task_prefix`. First
    # auto-generated ID is `TASK-001`.
    proj.run("task", "create", "Probe HATS-518 e2e").expect_ok()
    proj.run("task", "transition", "TASK-001", "plan").expect_ok()
    # The PLAN scaffold is empty; `transition execute` defaults to
    # strict_plan_check=True and would raise EmptyPlanError BEFORE our
    # guard fires. Fill the scaffold with arbitrary non-empty content so
    # the empty-plan check passes and execution proceeds to the guard.
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / "TASK-001" / "plan.md"
    )
    assert plan_path.exists(), f"expected plan scaffold at {plan_path}"
    plan_path.write_text("# Plan\n\nNon-empty plan body for e2e.\n")

    # Park HEAD on a feature branch and try to execute.
    _git(project, "checkout", "-b", "feat/parking")
    (
        proj.run("task", "transition", "TASK-001", "execute")
        .expect_failure()
        .expect_stdout_contains("Refused", "feat/parking", "master")
    )

    # Card untouched — still in plan on disk.
    show = proj.run("task", "show", "TASK-001").expect_ok()
    assert "state: plan" in show.stdout, (
        f"Card should remain in 'plan' state after refused transition; "
        f"`task show` output tail:\n{show.stdout[-400:]}"
    )

    # No worktree leak — only the main repo's worktree listed.
    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 1, (
        f"Refused transition must not create a worktree; got {len(wt_list)}:\n"
        + "\n".join(wt_list)
    )
