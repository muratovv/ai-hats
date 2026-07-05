"""HATS-635 — per-section plan gate at the real CLI boundary.

`ai-hats task transition <ID> execute` must BLOCK when a required plan
section is empty and NAME the offending section(s). This is the gated
test for `dev_rule_e2e_gate` (HATS-635 touches `src/ai_hats/state.py`
+ `src/ai_hats/cli/task.py`).

Fail-under-revert: under the pre-HATS-635 byte-equality `_is_empty_scaffold`,
a plan with ANY content (here: Requirements filled, the rest empty) is "not
the verbatim scaffold" → the gate PASSES (exit 0) → no
`Empty required section(s)` message. The assertions below then fail. The
block path needs no git: the gate raises BEFORE `_setup_worktree`.

The positive path (all sections filled → gate passes → worktree setup runs)
is exercised by the sibling e2e tests that now seed a full 4-section plan:
`test_task_transition_branch_exists.py` and `test_wt_create_base_guard_e2e.py`.

Harness mirrors `test_task_transition_branch_exists.py`: `python -m ai_hats`
with `PYTHONPATH=<checkout>/src`, so the test exercises the CURRENT checkout
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
from ai_hats.paths import PROJECT_CONFIG

# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


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
    """Role-less ai-hats project (no git — the block path never reaches
    worktree setup)."""
    p = tmp_path / "project"
    p.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(p / PROJECT_CONFIG)
    Assembler(p).init()
    return p


_PARTIAL_PLAN = (
    "# Plan for HATS-001: Probe\n\n"
    "## Requirements\nOnly this section is filled.\n\n"
    "## Scope & Out-of-scope\n\n"
    "## Steps\n\n"
    "## Verification Protocol\n\n"
)


def test_transition_execute_blocks_and_names_empty_sections(project: Path) -> None:
    r = _run_hats(project, "task", "create", "Probe", "--id", "HATS-001")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_hats(project, "task", "transition", "HATS-001", "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / "HATS-001" / "plan.md"
    )
    assert plan_path.exists(), f"expected scaffold at {plan_path}"
    plan_path.write_text(_PARTIAL_PLAN)

    r = _run_hats(project, "task", "transition", "HATS-001", "execute")
    assert r.returncode != 0, (
        "gate must BLOCK execute on a partial plan; got exit 0\n"
        f"stdout:\n{r.stdout}"
    )
    # The block message must NAME each empty required section...
    for marker in (
        "Empty required section(s)",
        "Scope & Out-of-scope",
        "Steps",
        "Verification Protocol",
    ):
        assert marker in r.stdout, (
            f"missing marker {marker!r} in:\n{r.stdout}"
        )
    # ...and must NOT list the one section that IS filled.
    assert "Requirements," not in r.stdout, (
        f"filled section must not be listed as empty:\n{r.stdout}"
    )
