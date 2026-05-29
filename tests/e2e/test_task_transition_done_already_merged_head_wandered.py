"""End-to-end coverage for ``ai-hats task transition <ID> done`` when the
task branch is ALREADY merged into its base and the main checkout HEAD has
wandered to a foreign branch (HATS-596).

Complement of ``test_task_transition_done_head_wandered.py`` (HATS-533):
there the work is NOT merged and a wandered HEAD MUST refuse (wrong-branch
merge risk). Here the work IS merged into base, so the main-repo HEAD
position is irrelevant — finalize MUST succeed via the checkout-independent
already-merged short-circuit instead of the false "base branch mismatch".

This is the exact shape the supervisor hit during the HATS-593 finalize:
the work was fully merged into master AND pushed, but the main checkout sat
on a concurrent feature branch with uncommitted WIP, and ``transition done``
refused with a false mid-merge / un-merged hint.

**Fail-under-revert**: remove the HATS-596 short-circuit in
``Worktree.merge()`` → the HATS-533 HEAD-mismatch guard fires →
``transition done`` exits 1 with "base branch mismatch", and the exit-0 /
``state: done`` assertions below fail.

Modelled on ``tests/e2e/test_task_transition_done_head_wandered.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


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
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


@pytest.mark.integration
def test_e2e_transition_done_already_merged_head_wandered(shared_launcher, tmp_path):
    """HATS-596 on the `task transition done` surface.

    Scenario (mirrors the HATS-593 incident):
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk brainstorm → plan → execute (worktree from
         base, `_original_branch=<base>`).
      4. Commit work on the worktree branch.
      5. Merge the branch into base in the main repo (`--no-ff`) — the work
         is now fully integrated, branch is an ancestor of base.
      6. In the main repo, ``git checkout -b wandered-feature`` + leave
         uncommitted WIP (HATS-593 live trigger).
      7. Walk execute → document → review.
      8. ``ai-hats task transition <ID> done`` MUST exit 0 (short-circuit).
      9. Task reaches ``done``; worktree dir + branch torn down.
     10. Main checkout untouched: still on wandered-feature, WIP intact.
     11. No double-merge: base ref unchanged since the manual merge.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )
    base_branch = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create task → execute (worktree from base) ----
    new_res = ai_hats(
        "task", "create", "already merged test",
        "--description", "exercise the HATS-596 already-merged short-circuit",
        "--role", "assistant",
        "--reviewer", "user",
    )
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
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Objective\nexercise already-merged short-circuit.\n\n"
        "## Steps\n- [ ] do thing\n"
    )
    ai_hats("task", "transition", task_id, "execute")

    # Locate worktree.
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
    task_branch = f"task/{task_id.lower()}"

    # ---- 3. commit work on the worktree branch ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )

    # ---- 4. INCIDENT setup: merge branch into base, THEN wander HEAD ----
    # The work is now fully integrated into base (as in the HATS-593
    # finalize where the branch was merged into master + pushed).
    _git(
        project, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "merge", "--no-ff", "--no-edit", task_branch,
    )
    base_sha_after_merge = _git(
        project, "rev-parse", base_branch
    ).stdout.strip()
    # Main checkout wanders to a foreign feature branch with uncommitted WIP.
    _git(project, "checkout", "-b", "wandered-feature")
    (project / "foreign-wip.txt").write_text("concurrent WIP\n")  # untracked

    # ---- 5. walk execute → document → review ----
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- 6. transition done MUST SUCCEED (the fix) ----
    res = ai_hats("task", "transition", task_id, "done", expect_exit=0)
    combined = res.stdout + res.stderr
    assert "base branch mismatch" not in combined.lower(), (
        f"false mismatch refusal — HATS-596 short-circuit not applied:\n"
        f"{combined}"
    )

    # ---- 7. task done; worktree dir + branch torn down ----
    show = ai_hats("task", "show", task_id)
    assert "state: done" in show.stdout, (
        f"task did not reach `done`:\n{show.stdout}"
    )
    assert not wt_path.exists(), f"worktree dir not removed: {wt_path}"
    branches = _git(project, "branch", "--list", task_branch).stdout.strip()
    assert branches == "", f"task branch not deleted: {branches!r}"

    # ---- 8. main checkout untouched: still wandered, WIP intact ----
    head_now = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert head_now == "wandered-feature", (
        f"main checkout HEAD moved (should be untouched): {head_now}"
    )
    assert (project / "foreign-wip.txt").read_text() == "concurrent WIP\n", (
        "foreign uncommitted WIP was clobbered"
    )

    # ---- 9. no double-merge: base ref unchanged ----
    assert _git(
        project, "rev-parse", base_branch
    ).stdout.strip() == base_sha_after_merge, (
        "base branch was re-merged — short-circuit should NOT run git merge"
    )
    wf_only = _git(
        project, "log", "--oneline", f"{base_branch}..wandered-feature"
    ).stdout.strip()
    assert wf_only == "", (
        f"wandered-feature got unexpected commits:\n{wf_only}"
    )
