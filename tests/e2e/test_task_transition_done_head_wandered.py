"""End-to-end coverage for ``ai-hats task transition <ID> done``
HEAD-wandered guard (HATS-533).

Sibling to ``test_wt_merge_head_wandered.py`` covering the
``task transition done`` surface specifically — per
``dev_rule_e2e_gate`` each CLI surface touched needs its own
real-subprocess test. This is the exact scenario that fired live in
the HATS-509 session: worktree created from master, peer agent moved
main-repo HEAD to a different branch, ``transition done`` merged into
the wrong branch.

**Fail-under-revert**: remove the ``except
WorktreeBaseBranchMismatchError`` handler in ``cli/task.py
task_transition`` (Step 4 of the HATS-533 plan) → the exception
propagates as an unhandled error, the recipe assertions below fail
with a Python traceback in the output instead.

Modelled on ``tests/e2e/test_task_transition_drift_message.py``.
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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_task_transition_done_head_wandered(shared_launcher, tmp_path):
    """HATS-533 on the `task transition done` surface.

    Scenario (mirrors the HATS-509 incident):
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk brainstorm → plan → execute (worktree
         created from master, `_original_branch=master`).
      4. Commit work on the worktree branch.
      5. In the main repo, ``git checkout -b wandered-feature``
         (HATS-509 live trigger).
      6. Walk execute → document → review.
      7. ``ai-hats task transition <ID> done`` MUST exit 1 with the
         mismatch message + the `git checkout master` recipe.
      8. Master is untouched (the bug we're guarding); wandered-feature
         is untouched too.
      9. Task remains in ``review`` (HATS-481 fail-loud).
     10. Recovery: ``git checkout master; ai-hats task transition done``
         succeeds.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

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

    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    base_branch = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create a task and walk it through to execute ----
    new_res = ai_hats(
        "task", "create", "wandered head test",
        "--description", "exercise the HATS-533 mismatch translation",
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
        "# Plan\n\n## Requirements\nexercise HEAD wandering.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
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

    # ---- 4. simulate HEAD wandering in the main repo ----
    _git(project, "checkout", "-b", "wandered-feature")

    # ---- 5. walk execute → document → review ----
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- 6. transition done MUST refuse ----
    res = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    # Positive: mismatch refusal surfaced with both branch names.
    assert "base branch mismatch" in combined.lower(), (
        f"mismatch refusal not surfaced:\n{combined}"
    )
    assert "wandered-feature" in combined, (
        f"current branch name missing from refusal:\n{combined}"
    )
    assert base_branch in combined, (
        f"expected branch name missing from refusal:\n{combined}"
    )

    # Positive: HATS-533 recipe — `git checkout <expected>` + retry.
    assert f"git checkout {base_branch}" in combined, (
        f"recovery `git checkout {base_branch}` missing from recipe:\n"
        f"{combined}"
    )
    assert f"ai-hats task transition {task_id} done" in combined, (
        f"retry step missing from recipe:\n{combined}"
    )
    assert str(project) in combined, (
        f"main-repo path missing from cd hint:\n{combined}"
    )

    # ---- 7. critical safety: NO wrong-branch merge happened ----
    wandered_log = _git(
        project, "log", "--oneline", "wandered-feature"
    ).stdout
    assert "wt-work" not in wandered_log, (
        f"wandered branch MUST NOT receive the worktree commit "
        f"(this is the HATS-509 live incident shape):\n{wandered_log}"
    )
    base_log = _git(project, "log", "--oneline", base_branch).stdout
    assert "wt-work" not in base_log, (
        f"base branch unexpectedly received the worktree commit:\n"
        f"{base_log}"
    )

    # ---- 8. card remains in `review` (HATS-481 fail-loud) ----
    show = ai_hats("task", "show", task_id)
    assert "state: review" in show.stdout, (
        f"task should remain in `review` after mismatch refusal:\n"
        f"{show.stdout}"
    )

    # ---- 9. recovery: switch back, transition done succeeds ----
    _git(project, "checkout", base_branch)
    ai_hats("task", "transition", task_id, "done")
    show2 = ai_hats("task", "show", task_id)
    assert "state: done" in show2.stdout, (
        f"task did not reach `done` after recovery:\n{show2.stdout}"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, (
        f"worktree commit not in history after recovery:\n{log}"
    )
