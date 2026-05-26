"""End-to-end coverage for ``ai-hats task transition <ID> done`` drift
message (HATS-509).

The inner ``wt merge`` invoked by ``transition done`` historically raised
a ``WorktreeDriftError`` whose message ended with "re-run with
``--accept-drift``". Users copy-pasted that as ``ai-hats task transition
<ID> done --accept-drift``, which fails with ``No such option`` — the
flag lives on ``wt merge``, not ``task transition``. HATS-509 moves the
recipe out of the exception body into CLI handlers and translates it
specifically for ``task transition done``.

Per ``dev_rule_e2e_gate``: this changes ``src/ai_hats/cli/task.py``,
so a real-subprocess e2e test is mandatory. Pipeline / CliRunner tests
do NOT satisfy the gate.

**Fail-under-revert**: remove the ``except WorktreeDriftError`` handler
in ``cli/task.py task_transition`` (Step 1 of HATS-509 plan) → the
generic handler at the end of ``task_transition`` no longer applies
either (the exception bubbles up to Click → exit code differs and the
message is the raw Python traceback / generic error). The recipe
assertions below fail.

Modelled on ``tests/e2e/test_wt_merge_drift.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_task_transition_done_drift_message(tmp_path):
    """HATS-509: drift message on ``task transition done`` names the
    correct command surface and gives a copy-pasteable two-step recipe.

    Scenario:
      1. Bootstrap launcher + ``self update`` + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk it brainstorm → plan → execute (this opens
         the worktree).
      4. Fill the plan, commit work on the worktree branch.
      5. In the main repo, advance the base branch (simulates drift —
         another agent merged).
      6. Walk the task execute → document → review.
      7. ``ai-hats task transition <ID> done`` — must exit 1 with a
         message naming the correct command (``ai-hats wt merge
         --accept-drift``) and the main-repo path, NOT advertising
         ``--accept-drift`` on ``task transition``.
      8. Task remains in ``review`` (fail-loud: HATS-481 keeps the card
         out of ``done`` until merge succeeds).
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    # ---- install launcher ----
    _run(
        ["bash", str(INSTALL_LAUNCHER)],
        cwd=tmp_path, env=env, timeout=30,
    )
    assert launcher_dest.is_file()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "update")
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- 2. create a task and walk it through to execute ----
    new_res = ai_hats(
        "task", "create", "drift message test",
        "--description", "exercise the HATS-509 translated message",
        "--role", "assistant",
        "--reviewer", "user",
    )
    # `task create` prints `Created: TST-NNN — <title> [...] (...)`
    task_id = None
    for line in new_res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Created:"):
            task_id = line.split()[1]
            break
    assert task_id and task_id.startswith("TST-"), (
        f"could not parse task ID from:\n{new_res.stdout}"
    )

    ai_hats("task", "transition", task_id, "plan")

    # Fill the plan scaffold so the plan→execute transition is allowed.
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Objective\nexercise drift translation.\n\n"
        "## Steps\n- [ ] do thing\n"
    )

    ai_hats("task", "transition", task_id, "execute")

    # Locate the worktree.
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    branch_suffix = f"/task/{task_id.lower()}"
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith(branch_suffix):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path for {task_id}:\n{listing}"
    )

    # ---- 3. commit some work on the worktree branch ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )

    # ---- 4. main repo advances → drift ----
    (project / "other.txt").write_text("from another agent\n")
    _git(project, "add", "other.txt")
    _git(project, "commit", "-m", "main: advance base while task was open")

    # ---- 5. walk execute → document → review ----
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- 6. transition done MUST fail with the translated message ----
    res = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    # Positive: drift summary preserved (commits + affected path).
    assert "drift" in combined.lower(), (
        f"drift not mentioned in refusal:\n{combined}"
    )
    assert "other.txt" in combined, (
        f"affected path not listed in refusal:\n{combined}"
    )

    # Positive: HATS-509 recipe — full command form pointing at the
    # right surface.
    assert "ai-hats wt merge --accept-drift" in combined, (
        f"recipe missing the full `wt merge --accept-drift` command:\n"
        f"{combined}"
    )
    assert str(project) in combined, (
        f"main-repo path missing from cd hint:\n{combined}"
    )
    # The recipe must also point back at the original transition command
    # so the user has a complete two-step path.
    assert f"ai-hats task transition {task_id} done" in combined, (
        f"recipe missing the retry step:\n{combined}"
    )

    # Negative guard: the misleading suggestion that --accept-drift is a
    # `task transition` flag MUST NOT appear (this is the regression
    # being fixed).
    assert f"task transition {task_id} done --accept-drift" not in combined, (
        f"misleading `task transition ... --accept-drift` form leaked:\n"
        f"{combined}"
    )

    # Positive: the disambiguation Note must be present so the operator
    # who skims the recipe still gets a direct callout that
    # `--accept-drift` belongs to a sibling command. Pins the
    # clarification in place against future copy edits.
    assert "belongs to `wt merge`" in combined, (
        f"disambiguation note missing — operator may still try the flag "
        f"on `task transition`:\n{combined}"
    )

    # ---- 7. card remains in `review` (HATS-481 fail-loud) ----
    show = ai_hats("task", "show", task_id)
    assert "state: review" in show.stdout, (
        f"task should remain in `review` after drift refusal:\n{show.stdout}"
    )
