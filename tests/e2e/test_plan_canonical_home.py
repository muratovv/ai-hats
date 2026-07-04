"""HATS-637 — canonical plan home at the real CLI boundary.

A plan is ALWAYS a task and ALWAYS lives at the one canonical path
`tasks/<ID>/plan.md`. The `.claude/plans → plan-sync` second write path is
removed. This is the gated test for `dev_rule_e2e_gate` (HATS-637 touches
`src/ai_hats/state.py` + `src/ai_hats/cli/task.py`).

Two assertions, each fails-under-revert:

* **No import.** A stray `.claude/plans/<NN>-*.md` present before
  `transition <ID> plan` is INERT — the canonical `plan.md` stays the empty
  scaffold. Reverting the engine change re-imports the stray (scaffold no
  longer matches) → assertion reds.
* **Command gone.** `ai-hats task plan-sync <ID>` exits non-zero ("No such
  command"). Reverting the CLI change makes the command exist again → the
  exit-code assertion reds.

Harness mirrors `test_plan_gate_per_section_e2e.py`: `python -m ai_hats` with
`PYTHONPATH=<checkout>/src`, so the test exercises the CURRENT checkout
(worktree-portable — the dev-venv editable `ai-hats` binary points at the main
checkout's src, NOT a linked worktree's, so it would mask worktree changes).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.state import PLAN_SCAFFOLD

pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"


def _run_hats(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` against the current checkout."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, existing_pp)
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(project_dir),
        capture_output=True, text=True, env=env, timeout=timeout,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Role-less ai-hats project (no git — transition→plan never needs it)."""
    p = tmp_path / "project"
    p.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(p / "ai-hats.yaml")
    Assembler(p).init()
    return p


def _plan_path(project: Path, task_id: str) -> Path:
    return (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )


def test_stray_claude_plan_is_not_imported(project: Path) -> None:
    r = _run_hats(project, "task", "create", "Probe", "--id", "HATS-001")
    assert r.returncode == 0, f"create failed: {r.stderr}"

    # A stray Plan-mode artifact in the OLD location, present before the
    # transition. Under the removed detour this would be moved over the
    # scaffold; now it must be inert.
    plans_dir = project / ".claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    stray = plans_dir / "001-stray.md"
    stray.write_text("# STRAY PLAN\n\nThis must never reach the task tree.\n")

    r = _run_hats(project, "task", "transition", "HATS-001", "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    plan_path = _plan_path(project, "HATS-001")
    assert plan_path.exists(), f"expected scaffold at {plan_path}"
    # The canonical plan is the untouched empty scaffold — the stray was NOT
    # imported (fails under revert: the detour would overwrite this).
    assert plan_path.read_text() == PLAN_SCAFFOLD.format(
        task_id="HATS-001", title="Probe"
    )
    assert "STRAY PLAN" not in plan_path.read_text()
    # The stray is left where it was — the engine no longer touches it.
    assert stray.exists(), "stray must not be moved out of .claude/plans"


def test_plan_sync_command_is_gone(project: Path) -> None:
    r = _run_hats(project, "task", "create", "Probe", "--id", "HATS-002")
    assert r.returncode == 0, f"create failed: {r.stderr}"

    # The second write path's entry point no longer exists (fails under
    # revert: the command would resolve and exit 0/2-on-no-match instead).
    r = _run_hats(project, "task", "plan-sync", "HATS-002")
    assert r.returncode != 0, (
        "plan-sync must no longer be a command; got exit 0\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "No such command" in r.stderr or "no such command" in r.stderr.lower()
