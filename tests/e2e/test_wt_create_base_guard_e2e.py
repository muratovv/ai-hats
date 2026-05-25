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
