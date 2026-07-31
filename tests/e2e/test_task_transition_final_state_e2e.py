"""End-to-end coverage for `rack transition --final-state` (HATS-723).

Audit finding 2b-F8 (HATS-698) fixed two coupled defects in the legacy click
wiring; rack carries both contracts (guard restored in HATS-1275):

- `--final-state` on a non-review target must refuse loudly (exit 1), not
  parse-then-drop.
- `--final-state` on the review target rides the transition's single lock
  window and is visible in the `context` read-back.

Re-pointed off the legacy `ai-hats task` CLI (HATS-1260; the wait-on-1275
noted here since HATS-1263 is over). Real launcher + real pip install +
real binaries, marked `integration`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_transition_final_state(shared_launcher, tmp_path):
    """HATS-723 `--final-state` contract on rack, real subprocess.

    1. Bootstrap: session-shared venv + self init (TST- prefix).
    2. Create a task.
    3. REJECT (fail-under-revert): `transition TST-001 plan --final-state "x"`
       must exit 1 — under the reverted guard the flag is silently dropped and
       the task transitions to plan (exit 0).
    4. RECORD: `transition TST-001 review --force --reason ... --final-state ...`
       (force relaxes the FSM arrow; review has no worktree side-effects) must
       persist final_state, visible in `rack context`.
    """
    launcher_dest, env, venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project,
            env=env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    def rack(*args, expect_exit=0, timeout=180):
        return _run(
            [str(venv / "bin" / "rack"), *args],
            cwd=project,
            env={**env, "AI_HATS_PLAN_ACK": "1"},
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- bootstrap project (venv is the session-shared build) ----
    ai_hats(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "claude",
        "--task-prefix",
        "TST",
    )

    # ---- create a task ----
    rack("create", "Reviewable", "--description", "task")
    res = rack("context", "TST-001")
    assert "TST-001" in res.stdout, f"TST-001 missing:\n{res.stdout}"

    # ---- 3. REJECT non-review target (fail-under-revert) ----
    rej = rack(
        "transition",
        "TST-001",
        "plan",
        "--final-state",
        "x",
        expect_exit=1,
    )
    combined = (rej.stdout + rej.stderr).lower()
    assert "final" in combined, (
        f"reject message did not mention the flag:\n{rej.stdout}\n{rej.stderr}"
    )
    # The rejected transition must NOT have moved the task off brainstorm.
    res = rack("context", "TST-001")
    assert "state: brainstorm" in res.stdout, f"rejected transition mutated state:\n{res.stdout}"

    # ---- 4. RECORD on the review target (force relaxes FSM, no worktree) ----
    rack(
        "transition",
        "TST-001",
        "review",
        "--force",
        "--reason",
        "e2e reach review",
        "--final-state",
        "shipped feature X",
    )
    res = rack("context", "TST-001")
    assert "state: review" in res.stdout, f"not in review:\n{res.stdout}"
    assert "final_state: shipped feature X" in res.stdout, (
        f"final_state not recorded:\n{res.stdout}"
    )
