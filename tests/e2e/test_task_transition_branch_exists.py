"""HATS-517 — `ai-hats task transition <ID> execute` must handle the case
where the target branch (``task/<id-lower>``) already exists.

Three sub-cases (see plan + task card):

* **Case A** — branch exists, no worktree owns it. Transition must
  attach the existing branch to a new linked worktree → exit 0.
* **Case B** — branch is currently checked out in the MAIN worktree.
  Transition must refuse with an actionable hint → exit 2 (distinct
  from generic ``ValueError`` exit 1).
* **Case C** is exercised at unit-test level
  (``tests/test_worktree.py::TestBranchExistsClassifier``) — it
  requires a manual linked-worktree setup that doesn't add coverage
  at the subprocess boundary.

Pattern (subprocess + ``python -m ai_hats``) mirrors
``tests/e2e/test_wt_merge_ambiguity_guard.py`` — keeps the test
checkout-independent (works from main repo or a linked worktree, no
installed ``ai-hats`` binary required).

dev_rule_e2e_gate (HATS-517 touches ``src/ai_hats/worktree.py`` +
``src/ai_hats/cli/task.py``): this file is the gated test. Sanity:
under ``git stash`` of the HATS-517 classifier in
``WorktreeManager.create()``, both tests fail with the original
``WorktreeCreateError: branch already exists`` from
``git worktree add -b ...``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig


pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        check=True, capture_output=True, text=True,
    )


def _run_hats(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` against the current checkout."""
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC}:{existing_pp}" if existing_pp else str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(project_dir),
        capture_output=True, text=True, env=env, timeout=timeout,
    )


@pytest.fixture
def initialised_git_project(tmp_path: Path) -> Path:
    """Tmp dir bootstrapped as both an ai-hats project AND a git repo."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(
        project / "ai-hats.yaml"
    )
    Assembler(project).init()
    _git(project, "init")
    _git(project, "config", "user.email", "e2e@hats-517.test")
    _git(project, "config", "user.name", "HATS-517")
    (project / "README.md").write_text("# hats-517\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    return project


def _create_and_plan(project: Path, task_id: str) -> None:
    """Create task ``task_id`` and walk brainstorm → plan with a non-empty plan."""
    r = _run_hats(project, "task", "create", "test task",
                  "--id", task_id, "--description", "e2e")
    assert r.returncode == 0, f"task create failed: {r.stderr}"
    r = _run_hats(project, "task", "transition", task_id, "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"
    # Overwrite the scaffold so the EmptyPlanError gate in
    # `transition execute` lets us through to the worktree-setup path
    # (which is what HATS-517 fixes).
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        "# Plan\n\n## Objective\nE2E\n\n## Steps\n- [ ] do thing\n"
    )


def test_case_a_pre_existing_branch_attaches(
    initialised_git_project: Path,
) -> None:
    """Case A: `git branch task/hats-517a` ahead of time; transition succeeds."""
    proj = initialised_git_project
    task_id = "HATS-517A"
    _create_and_plan(proj, task_id)

    # Pre-create the branch the transition is about to use.
    _git(proj, "branch", "task/hats-517a")

    r = _run_hats(proj, "task", "transition", task_id, "execute")
    assert r.returncode == 0, (
        f"Case A must succeed; got exit {r.returncode}\n"
        f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    # The transition CLI prints the worktree path + branch on success.
    combined = r.stdout + r.stderr
    assert "task/hats-517a" in combined, (
        f"branch name not surfaced in output: {combined}"
    )
    assert "Worktree:" in combined, (
        f"worktree path not surfaced in output: {combined}"
    )

    # Verify the linked worktree exists and is on the right branch.
    wt_list = _git(proj, "worktree", "list", "--porcelain").stdout
    assert "branch refs/heads/task/hats-517a" in wt_list, (
        f"expected linked worktree on task/hats-517a, got:\n{wt_list}"
    )


def test_case_b_branch_checked_out_in_main_refuses(
    initialised_git_project: Path,
) -> None:
    """Case B: main worktree already on the target branch → refuse + hint."""
    proj = initialised_git_project
    task_id = "HATS-517B"
    _create_and_plan(proj, task_id)

    # Check out the target branch in the main worktree (the repro).
    _git(proj, "checkout", "-b", "task/hats-517b")

    r = _run_hats(proj, "task", "transition", task_id, "execute")
    assert r.returncode == 2, (
        f"Case B must exit 2 (worktree refusal), got {r.returncode}\n"
        f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    combined = r.stdout + r.stderr
    # Actionable hint surface — message body from WorktreeCreateError.
    assert "checked out in the main worktree" in combined, (
        f"expected Case B hint, got:\n{combined}"
    )
    assert "git switch" in combined, (
        f"expected git-switch alternative in hint, got:\n{combined}"
    )
    assert "task close" in combined, (
        f"expected task-close alternative in hint, got:\n{combined}"
    )
