"""e2e (HATS-517, HATS-518, HATS-1263)

flow:   a developer transitioning a task to execute when its target git branch already
        exists
cmds:
    # when target task branch already exists in the repository
    rack transition HATS-517A execute
expect: the existing git branch is attached to a newly created worktree directory when
        not currently checked out in main
why:    transition to execute must reuse existing task branches safely without failing
        on pre-existing git branch references
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


pytestmark = [pytest.mark.integration, pytest.mark.wt]


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"


def _run_rack(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats_rack <args>`` against the current checkout."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, existing_pp)
    # Consent-gated on rack; this test is about the worktree path behind it.
    env["AI_HATS_PLAN_ACK"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def initialised_git_project(tmp_path: Path) -> Path:
    """Tmp dir bootstrapped as both an ai-hats project AND a git repo."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(project / PROJECT_CONFIG)
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
    r = _run_rack(project, "create", "test task", "--id", task_id, "--description", "e2e")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_rack(project, "transition", task_id, "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"
    # Overwrite the scaffold so the EmptyPlanError gate in
    # `transition execute` lets us through to the worktree-setup path
    # (which is what HATS-517 fixes).
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
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
    # A letter-suffixed id on purpose: it is what the real backlog once used, and
    # rack must route it (HATS-1283 widened `ids._ID_RE`).
    task_id = "HATS-517A"
    _create_and_plan(proj, task_id)

    # Pre-create the branch the transition is about to use.
    _git(proj, "branch", "task/hats-517a")

    r = _run_rack(proj, "transition", task_id, "execute")
    assert r.returncode == 0, (
        f"Case A must succeed; got exit {r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    # rack surfaces the branch through the worktree dirname it prints
    # (`branch_name.replace("/", "-")` — manager.py); there is no `Branch:` line.
    combined = r.stdout + r.stderr
    assert "task-hats-517a" in combined, f"branch name not surfaced in output: {combined}"
    assert "Worktree:" in combined, f"worktree path not surfaced in output: {combined}"

    # Verify the linked worktree exists and is on the right branch.
    wt_list = _git(proj, "worktree", "list", "--porcelain").stdout
    assert "branch refs/heads/task/hats-517a" in wt_list, (
        f"expected linked worktree on task/hats-517a, got:\n{wt_list}"
    )


# NOTE: Case B at the CLI boundary is covered by HATS-518's
# `assert_head_is_canonical_base()` guard inside `state._setup_worktree`
# — that guard fires BEFORE `WorktreeManager.create()`, so the HATS-517
# Case B classifier inside `create()` is never reached via the
# `rack transition <ID> execute` path. The Case B classifier remains
# in place as defense-in-depth for direct Python-API callers; coverage
# lives at unit level in
# `tests/test_worktree.py::TestBranchExistsClassifier::test_case_b_refuse_when_checked_out_in_main`.
