"""End-to-end coverage for task lifecycle CLI surface added in HATS-371.

The unit suite covers the TaskCard model + state machine, but stubs the
click wiring that users actually invoke. HATS-371 added four new contracts
on `ai-hats task`:

- `task close <id> --resolution "..."` — fast-close brainstorm/plan → done
- `task link <FROM> <TO> [--type related|see-also|fold]`
- `task unlink <FROM> <TO>`
- `task transition --force --reason "..."`

Per `dev_rule_e2e_gate`, CLI surface additions require a real-subprocess
test that would fail if the click wiring drifted (command moved between
groups, option renamed, exit-code change). This test runs the **real**
launcher + **real** pip install + **real** ai-hats binary. Slow (~60s
on a warm pip cache). Marked `integration`.
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
def test_e2e_task_close_link_force(shared_launcher, tmp_path):
    """HATS-371 task CLI surface, real subprocess.

    1. Bootstrap: session-shared venv + self init (TST- prefix).
    2. Create two tasks via `task create`.
    3. `task close TST-001 --resolution "..."` — fast-close brainstorm → done.
    4. `task link TST-002 TST-001 --type related` — symmetric cross-ref.
    5. `task unlink TST-002 TST-001` — removes the link.
    6. `task transition TST-001 brainstorm --force --reason "..."` — bypass FSM.
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

    # ---- create two tasks ----
    ai_hats("task", "create", "First", "-d", "first task", "-p", "medium")
    ai_hats("task", "create", "Second", "-d", "second task", "-p", "medium")

    res = ai_hats("task", "list", "--all")
    assert "TST-001" in res.stdout, f"TST-001 missing:\n{res.stdout}"
    assert "TST-002" in res.stdout, f"TST-002 missing:\n{res.stdout}"

    # ---- 3. fast-close (HATS-371: ai-hats task close) ----
    ai_hats("task", "close", "TST-001", "--resolution", "shipped on master")
    res = ai_hats("task", "show", "TST-001")
    assert "state: done" in res.stdout, f"TST-001 not done:\n{res.stdout}"
    assert "shipped on master" in res.stdout, (
        f"resolution not recorded:\n{res.stdout}"
    )

    # close without --resolution must fail (required flag)
    ai_hats(
        "task", "close", "TST-002",
        expect_exit=2,  # click missing-required-option
    )

    # ---- 4. link TST-002 → TST-001 (related, symmetric) ----
    ai_hats("task", "link", "TST-002", "TST-001", "--type", "related")
    res_from = ai_hats("task", "show", "TST-002")
    res_to = ai_hats("task", "show", "TST-001")
    # Both ends mention the peer id (related is symmetric).
    assert "TST-001" in res_from.stdout, (
        f"outbound link missing on TST-002:\n{res_from.stdout}"
    )
    assert "TST-002" in res_to.stdout, (
        f"inbound link missing on TST-001:\n{res_to.stdout}"
    )

    # ---- 5. unlink ----
    ai_hats("task", "unlink", "TST-002", "TST-001")
    res_from = ai_hats("task", "show", "TST-002")
    # After unlink, the outbound link section should not list TST-001.
    # Heuristic: count peer mentions — should drop after unlink.
    # Conservative check: any "related" rendering line should no longer
    # carry TST-001 on TST-002's card.
    lower = res_from.stdout.lower()
    assert "related" not in lower or "tst-001" not in lower, (
        f"unlink did not remove related link:\n{res_from.stdout}"
    )

    # ---- 6. force transition done → brainstorm ----
    # Normal transition would be rejected by FSM guard.
    ai_hats(
        "task", "transition", "TST-001", "brainstorm",
        "--force", "--reason", "e2e reopen test",
    )
    res = ai_hats("task", "show", "TST-001")
    assert "state: brainstorm" in res.stdout, (
        f"force transition did not apply:\n{res.stdout}"
    )

    # --force without --reason must be rejected (custom check → exit 1).
    ai_hats(
        "task", "transition", "TST-001", "plan", "--force",
        expect_exit=1,
    )
