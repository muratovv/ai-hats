"""e2e (HATS-621, HATS-1263)

flow:   a developer transitioning a task to execute with an empty optional Approach &
        counter plan section
cmds:
    rack transition HATS-621S plan
expect: the plan scaffold includes the Approach & counter heading and transition to
        execute succeeds when required sections are filled even if Approach &
        counter is empty
why:    the Approach & counter section provides structured design evaluation but must
        remain optional to avoid blocking straightforward task execution
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

pytestmark = [pytest.mark.integration, pytest.mark.rack]


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
    # Consent-gated on rack; this test is about the optional section behind it.
    env["AI_HATS_PLAN_ACK"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _plan_path(project: Path, task_id: str) -> Path:
    return project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Tmp dir bootstrapped as both an ai-hats project AND a git repo."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(project / PROJECT_CONFIG)
    Assembler(project).init()
    _git(project, "init")
    _git(project, "config", "user.email", "e2e@hats-621.test")
    _git(project, "config", "user.name", "HATS-621")
    (project / "README.md").write_text("# hats-621\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    return project


def test_scaffold_contains_approach_counter_in_position_c(git_project: Path) -> None:
    """The real binary's plan scaffold carries `## Approach & counter`, between
    Requirements and Scope (fail-under-revert anchor)."""
    proj = git_project
    # A letter-suffixed id on purpose: it is what the real backlog once used, and
    # rack must route it (HATS-1283 widened `ids._ID_RE`).
    r = _run_rack(proj, "create", "Probe", "--id", "HATS-621S")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_rack(proj, "transition", "HATS-621S", "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    scaffold = _plan_path(proj, "HATS-621S").read_text()
    assert "## Approach & counter" in scaffold, (
        f"scaffold missing the conditional stage heading:\n{scaffold}"
    )
    # Position C: after Requirements, before Scope & Out-of-scope.
    i_req = scaffold.index("## Requirements")
    i_ac = scaffold.index("## Approach & counter")
    i_scope = scaffold.index("## Scope & Out-of-scope")
    assert i_req < i_ac < i_scope, (
        f"Approach & counter must sit after Requirements and before Scope:\n{scaffold}"
    )


def test_empty_approach_counter_does_not_block_execute(git_project: Path) -> None:
    """All REQUIRED sections filled + an EMPTY `## Approach & counter` still
    transitions to execute (the section is optional, never gate-blocking)."""
    proj = git_project
    task_id = "HATS-6212"
    r = _run_rack(proj, "create", "Probe", "--id", task_id, "--description", "e2e")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_rack(proj, "transition", task_id, "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    _plan_path(proj, task_id).write_text(
        f"# Plan for {task_id}: Probe\n\n"
        "## Requirements\nShip the value-counter stage.\n\n"
        "## Approach & counter\n\n"  # deliberately empty — optional
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    r = _run_rack(proj, "transition", task_id, "execute")
    assert r.returncode == 0, (
        "an empty OPTIONAL section must not block execute; got exit "
        f"{r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "Worktree:" in combined, f"expected worktree setup on a passing gate:\n{combined}"
