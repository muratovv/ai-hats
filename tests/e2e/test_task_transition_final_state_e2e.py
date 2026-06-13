"""End-to-end coverage for `task transition --final-state` (HATS-723).

Audit finding 2b-F8 (HATS-698) fixed two coupled defects in the click wiring:

- `--final-state` on a non-review target was parsed and silently dropped
  (option-parsed-then-ignored). It must now refuse loudly (exit 1).
- `--final-state` on the review target was written in a separate lock BEFORE
  the transition; it now rides the transition's single lock window.

Per `dev_rule_e2e_gate`, the `src/ai_hats/cli/` surface change needs a real
subprocess test that fails if the guard is reverted. This test runs the
**real** launcher + **real** pip install + **real** ai-hats binary. Slow
(~60s on a warm pip cache). Marked `integration`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_transition_final_state(shared_launcher, tmp_path):
    """HATS-723 `--final-state` contract, real subprocess.

    1. Bootstrap: session-shared venv + self init (TST- prefix).
    2. Create a task.
    3. REJECT (fail-under-revert): `transition TST-001 plan --final-state "x"`
       must exit 1 — under the reverted code the flag is silently dropped and
       the task transitions to plan (exit 0).
    4. RECORD: `transition TST-001 review --force --reason ... --final-state ...`
       (force bypasses the FSM guard; review has no worktree side-effects) must
       persist final_state, visible in `task show`.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project (venv is the session-shared build) ----
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- create a task ----
    ai_hats("task", "create", "Reviewable", "-d", "task", "-p", "medium")
    res = ai_hats("task", "list", "--all")
    assert "TST-001" in res.stdout, f"TST-001 missing:\n{res.stdout}"

    # ---- 3. REJECT non-review target (fail-under-revert) ----
    rej = ai_hats(
        "task", "transition", "TST-001", "plan", "--final-state", "x",
        expect_exit=1,
    )
    assert "final-state" in rej.stdout.lower(), (
        f"reject message did not mention the flag:\n{rej.stdout}"
    )
    # The rejected transition must NOT have moved the task off brainstorm.
    res = ai_hats("task", "show", "TST-001")
    assert "state: brainstorm" in res.stdout, (
        f"rejected transition mutated state:\n{res.stdout}"
    )

    # ---- 4. RECORD on the review target (force bypasses FSM, no worktree) ----
    ai_hats(
        "task", "transition", "TST-001", "review",
        "--force", "--reason", "e2e reach review",
        "--final-state", "shipped feature X",
    )
    res = ai_hats("task", "show", "TST-001")
    assert "state: review" in res.stdout, f"not in review:\n{res.stdout}"
    assert "final_state: shipped feature X" in res.stdout, (
        f"final_state not recorded:\n{res.stdout}"
    )
