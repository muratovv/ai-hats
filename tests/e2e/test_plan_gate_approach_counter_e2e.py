"""HATS-621 — the conditional "Approach & counter" stage at the real CLI boundary.

M3 adds ``Section(name="Approach & counter", required=False)`` to
``PLAN_SECTIONS`` (``src/ai_hats/state.py``). Two real-binary guarantees:

1. The scaffold the binary writes on ``transition <ID> plan`` carries the
   ``## Approach & counter`` heading in position C — after ``Requirements``,
   before ``Scope & Out-of-scope``.
   **Fail-under-revert:** drop the ``Section`` → the heading is absent →
   ``test_scaffold_contains_approach_counter_in_position_c`` fails.
2. The section is OPTIONAL: a plan that fills every REQUIRED section but leaves
   ``## Approach & counter`` empty still passes the plan→execute gate (exit 0).
   Guards against anyone flipping it to ``required=True``.

``dev_rule_e2e_gate`` note: M3 touches ``src/ai_hats/state.py``, which is not
literally under the rule's ``cli/**`` trigger. This e2e is added per the task's
explicit acceptance — the real-binary scaffold is the user-facing contract.

Harness mirrors ``test_plan_gate_per_section_e2e.py`` /
``test_task_transition_branch_exists.py``: ``python -m ai_hats`` with
``PYTHONPATH=<checkout>/src`` exercises the CURRENT checkout.
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


def _plan_path(project: Path, task_id: str) -> Path:
    return (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Tmp dir bootstrapped as both an ai-hats project AND a git repo."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(
        project / "ai-hats.yaml"
    )
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
    r = _run_hats(proj, "task", "create", "Probe", "--id", "HATS-621S")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_hats(proj, "task", "transition", "HATS-621S", "plan")
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
        "Approach & counter must sit after Requirements and before Scope:\n"
        f"{scaffold}"
    )


def test_empty_approach_counter_does_not_block_execute(git_project: Path) -> None:
    """All REQUIRED sections filled + an EMPTY `## Approach & counter` still
    transitions to execute (the section is optional, never gate-blocking)."""
    proj = git_project
    task_id = "HATS-621E"
    r = _run_hats(proj, "task", "create", "Probe", "--id", task_id,
                  "--description", "e2e")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_hats(proj, "task", "transition", task_id, "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    _plan_path(proj, task_id).write_text(
        "# Plan for HATS-621E: Probe\n\n"
        "## Requirements\nShip the value-counter stage.\n\n"
        "## Approach & counter\n\n"  # deliberately empty — optional
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    r = _run_hats(proj, "task", "transition", task_id, "execute")
    assert r.returncode == 0, (
        "an empty OPTIONAL section must not block execute; got exit "
        f"{r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "Worktree:" in combined, (
        f"expected worktree setup on a passing gate:\n{combined}"
    )
