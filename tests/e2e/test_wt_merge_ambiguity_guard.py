"""HATS-502 / HATS-482 (R-08) — `ai-hats wt merge` ambiguity guard.

Repro from HATS-502 (foot-gun observed during HATS-496 merge):

    1. Two linked worktrees open: `task/hats-A`, `task/hats-B`.
    2. cd to the main repo (NOT inside any worktree).
    3. Run `ai-hats wt merge` with no BRANCH argument.

Pre-HATS-482 result: silent pick of `list_active()[0]` (alphabetically
first), wrong-target merge, recovery via `git merge --abort`.

Post-HATS-482: `_resolve_worktree` raises `click.UsageError` when more
than one worktree is tracked and BRANCH is omitted — non-zero exit + a
message naming all candidate branches so the operator can disambiguate.

Free-tier (no agent spawn) — bare `git init` + two `wt create`s + one
bad `wt merge` invocation. Wall budget < 5s.

Why not the ``tmp_project`` fixture? That fixture binds to
``<repo_root>/.venv/bin/ai-hats`` which only exists in the main checkout,
not in a linked worktree. This test invokes ``python -m ai_hats``
directly via the current interpreter + an explicit ``PYTHONPATH`` so it
runs from either location without needing an installed binary.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.models import ProjectConfig
from ai_hats.assembler import Assembler


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _run_hats(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` in ``project_dir`` with PYTHONPATH=src.

    Picks up the current checkout (worktree or main) — independent of any
    installed ``ai-hats`` binary.
    """
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC}:{existing_pp}" if existing_pp else str(SRC)
    )
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def initialised_git_project(tmp_path: Path) -> Path:
    """Tmp dir bootstrapped as an ai-hats project AND a git repo with one commit."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(
        project / "ai-hats.yaml"
    )
    Assembler(project).init()
    _git(project, "init")
    _git(project, "config", "user.email", "e2e@hats-502.test")
    _git(project, "config", "user.name", "HATS-502")
    (project / "README.md").write_text("# hats-502\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    return project


def test_wt_merge_no_branch_with_multiple_active_refuses(
    initialised_git_project: Path,
) -> None:
    """`ai-hats wt merge` (no BRANCH, ≥2 active worktrees, outside any wt)
    exits non-zero and names the candidate branches."""
    proj = initialised_git_project

    # Two distinct linked worktrees — recreates the HATS-496 shape.
    r = _run_hats(proj, "wt", "create", "task/hats-aaa")
    assert r.returncode == 0, f"wt create aaa failed: {r.stderr}"
    r = _run_hats(proj, "wt", "create", "task/hats-bbb")
    assert r.returncode == 0, f"wt create bbb failed: {r.stderr}"

    # Bad invocation: outside any linked worktree, no BRANCH.
    result = _run_hats(proj, "wt", "merge")
    assert result.returncode != 0, (
        f"expected non-zero exit, got 0\nSTDOUT: {result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "Multiple active worktrees" in combined, (
        f"expected ambiguity error, got:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "task/hats-aaa" in combined
    assert "task/hats-bbb" in combined


def test_wt_merge_no_branch_with_single_active_proceeds(
    initialised_git_project: Path,
) -> None:
    """One active worktree + no BRANCH → still picks it (convenience preserved).

    R-08 fix deliberately leaves single-wt convenience intact; only the
    >1 case raises. This test guards against an over-strict regression
    that would refuse even the unambiguous case.
    """
    proj = initialised_git_project

    r = _run_hats(proj, "wt", "create", "task/hats-solo")
    assert r.returncode == 0, f"wt create failed: {r.stderr}"

    # `wt merge` with no BRANCH and only one active wt — must not raise
    # the ambiguity error. The merge itself is a no-op (empty wt = same
    # SHA as base) → succeeds.
    result = _run_hats(proj, "wt", "merge")
    combined = result.stdout + result.stderr
    assert "Multiple active worktrees" not in combined, (
        f"single-wt path raised ambiguity error (regression): {combined}"
    )
    assert result.returncode == 0, (
        f"expected success, got {result.returncode}\nSTDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}"
    )
