"""HATS-517 — `ai-hats task transition <ID> execute` must handle the case
where the target branch (``task/<id-lower>``) already exists.

Three sub-cases (see plan + task card):

* **Case A** — branch exists, no worktree owns it. Transition must
  attach the existing branch to a new linked worktree → exit 0.
  Exercised here at the CLI boundary.
* **Case B** — branch is currently checked out in the MAIN worktree.
  After HATS-518 landed on master, ``_setup_worktree`` calls
  ``assert_head_is_canonical_base()`` BEFORE ``WorktreeManager.create()``,
  so this path is intercepted with ``WorktreeBaseBranchError`` (exit 1)
  long before the HATS-517 classifier runs. The classifier remains as
  defense-in-depth for direct ``WorktreeManager().create()`` callers
  (Python API, tests) — exercised at unit level in
  ``tests/test_worktree.py::TestBranchExistsClassifier``.
* **Case C** is exercised at unit-test level (same class) — it requires
  a manual linked-worktree setup that doesn't add coverage at the
  subprocess boundary.

Pattern (subprocess + ``python -m ai_hats``) mirrors
``tests/e2e/test_wt_merge_ambiguity_guard.py`` — keeps the test
checkout-independent (works from main repo or a linked worktree, no
installed ``ai-hats`` binary required).

dev_rule_e2e_gate (HATS-517 touches ``src/ai_hats/wt/manager.py`` +
``src/ai_hats/cli/task.py``): this file is the gated test. Sanity:
under ``git stash`` of the HATS-517 classifier in
``WorktreeManager.create()``, Case A fails with the original
``WorktreeCreateError: branch already exists`` from
``git worktree add -b ...``.

Deliberate long e2e scenario contract — noqa: comment-length.
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
        "# Plan\n\n"
        "## Requirements\nE2E\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
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


# NOTE: Case B at the CLI boundary is covered by HATS-518's
# `assert_head_is_canonical_base()` guard inside `state._setup_worktree`
# — that guard fires BEFORE `WorktreeManager.create()`, so the HATS-517
# Case B classifier inside `create()` is never reached via the
# `task transition execute` path. The HATS-517 Case B classifier remains
# in place as defense-in-depth for direct Python-API callers; coverage
# lives at unit level in
# `tests/test_worktree.py::TestBranchExistsClassifier::test_case_b_refuse_when_checked_out_in_main`.
