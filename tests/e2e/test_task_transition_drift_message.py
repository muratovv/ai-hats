"""e2e (HATS-509, HATS-1274, HATS-1307)

flow:   a developer finalizing a task when the base branch has advanced with new
        commits since the task worktree was created
cmds:
    # when base branch has advanced with new commits
    rack transition TST-001 done
expect: transition to done is refused with detailed drift information and instructions
        to rebase or run ai-hats wt merge --accept-drift
why:    branch drift must be reported with copy-pasteable resolution steps to prevent
        unintended merge overwrites
"""
# comment-length: allow — fail-under-revert contract, dev_rule_e2e_gate §4

from __future__ import annotations
from _helpers.env import consent_grant
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt


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
def test_e2e_rack_transition_done_drift_message(shared_launcher, tmp_path):
    """HATS-1274: the drift refusal on rack names the correct command surface
    and gives a copy-pasteable recipe.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk it brainstorm → plan → execute (this opens
         the worktree).
      4. Fill the plan, commit work on the worktree branch.
      5. In the main repo, advance the base branch (simulates drift —
         another agent merged).
      6. Walk the task execute → document → review.
      7. ``rack transition <ID> done`` — must exit 1 with a message naming
         ``ai-hats wt merge --accept-drift`` and the main-repo path, NOT
         advertising ``--accept-drift`` on ``rack transition``.
      8. Task remains in ``review`` (fail-loud: HATS-481 keeps the card
         out of ``done`` until merge succeeds).
    """
    launcher_dest, env, venv = shared_launcher
    rack_bin = venv / "bin" / "rack"
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd,
            env=env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    def rack(*args, expect_exit=0, timeout=180, cwd=project, extra_env=None):
        return _run(
            [str(rack_bin), *args],
            cwd=cwd,
            env={**env, **(extra_env or {})},
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

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

    # ---- 2. create a task and walk it through to execute ----
    new_res = rack(
        "create",
        "drift message test",
        "--description",
        "exercise the ported drift recipe",
        "--role",
        "assistant",
        "--reviewer",
        "user",
    )
    # `rack create` prints `Created: TST-NNN — <title> [...] (...)`
    task_id = None
    for line in new_res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Created:"):
            task_id = line.split()[1]
            break
    assert task_id and task_id.startswith("TST-"), (
        f"could not parse task ID from:\n{new_res.stdout}"
    )

    rack("transition", task_id, "plan")

    # Fill the plan scaffold so the plan→execute transition is allowed.
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise drift translation.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    rack("transition", task_id, "execute", extra_env={"AI_HATS_PLAN_ACK": "1"})

    # Locate the worktree.
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    branch_suffix = f"/task/{task_id.lower()}"
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch ") :].strip()
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
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "wt-work",
    )

    # ---- 4. main repo advances → drift ----
    (project / "other.txt").write_text("from another agent\n")
    _git(project, "add", "other.txt")
    _git(project, "commit", "-m", "main: advance base while task was open")

    # ---- 5. walk execute → document → review ----
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- 6. transition done MUST fail with the ported recipe ----
    # HATS-1682: `review -> done` is a consent point the role declares. The
    # answer is scaffolding here — what this test measures is what the
    # WORKTREE layer does with the edge once consent is in hand.
    res = rack("transition", task_id, "done", expect_exit=1, extra_env=consent_grant())
    combined = res.stdout + res.stderr

    # Positive: drift summary preserved (commits + affected path).
    assert "drift" in combined.lower(), f"drift not mentioned in refusal:\n{combined}"
    assert "other.txt" in combined, f"affected path not listed in refusal:\n{combined}"

    # Positive (HATS-1307): the recipe leads with the rebase — the only remedy
    # `rack transition done` can actually complete, since it takes no flags.
    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert f"git rebase {base_branch}" in combined, f"recipe missing the rebase step:\n{combined}"
    assert f"cd {wt_path}" in combined, f"recipe missing the worktree cd:\n{combined}"

    # Positive: the fallback — full command form pointing at the right surface.
    assert f"ai-hats wt merge --accept-drift task/{task_id.lower()}" in combined, (
        f"recipe missing the full `wt merge --accept-drift` command:\n{combined}"
    )
    assert str(project) in combined, f"main-repo path missing from the cd hint:\n{combined}"
    # The recipe must also point back at the rack transition so the operator
    # has a complete two-step path.
    assert f"rack transition {task_id} --state done" in combined, (
        f"recipe missing the retry step:\n{combined}"
    )

    # Negative guard: --accept-drift MUST NOT be advertised as a rack
    # transition flag — the regression this recipe exists to prevent.
    assert "--state done --accept-drift" not in combined, (
        f"misleading `rack transition ... --accept-drift` form leaked:\n{combined}"
    )

    # Positive: the disambiguation Note must be present so an operator who
    # skims the recipe still gets a direct callout that `--accept-drift`
    # belongs to a sibling command. Pins it against future copy edits.
    assert "belongs to `wt merge`, not `rack transition`" in combined, (
        f"disambiguation note missing — operator may still try the flag on "
        f"`rack transition`:\n{combined}"
    )

    # ---- 7. card remains in `review` (HATS-481 fail-loud) ----
    show = rack("context", task_id)
    assert "state: review" in show.stdout, (
        f"task should remain in `review` after drift refusal:\n{show.stdout}"
    )
