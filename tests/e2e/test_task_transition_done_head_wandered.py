"""e2e (HATS-509, HATS-533, HATS-1274)

flow:   a developer finalizing a task when main repository HEAD is checked out on a
        different branch than base
cmds:
    # when main HEAD is checked out on a different branch than base
    rack transition TST-001 done
expect: transition to done is refused with an error detailing the HEAD mismatch and
        displaying the git checkout command to fix it
why:    merging a task worktree when main repository HEAD has wandered risks merging
        into the wrong target branch
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
def test_e2e_rack_transition_done_head_wandered(shared_launcher, tmp_path):
    """HATS-1274: the base-branch-mismatch refusal on rack carries the
    `git checkout <base>` recovery recipe.

    Scenario (mirrors the HATS-509 incident):
      1. Bootstrap + ``git init``, initial commit.
      2. Create a task, walk brainstorm → plan → execute (worktree
         created from base, `_original_branch=<base>`).
      3. Commit work on the worktree branch.
      4. In the main repo, ``git checkout -b wandered-feature``.
      5. Walk execute → document → review.
      6. ``rack transition <ID> done`` MUST exit 1 with the mismatch
         message + the `git checkout <base>` recipe.
      7. Base is untouched (the bug being guarded); so is
         wandered-feature.
      8. Task remains in ``review`` (HATS-481 fail-loud).
      9. Recovery: ``git checkout <base>`` then the transition succeeds.
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

    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create a task and walk it through to execute ----
    new_res = rack(
        "create",
        "wandered head test",
        "--description",
        "exercise the ported HATS-533 recipe",
        "--role",
        "assistant",
        "--reviewer",
        "user",
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

    rack("transition", task_id, "plan")

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise HEAD wandering.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    rack("transition", task_id, "execute", extra_env={"AI_HATS_PLAN_ACK": "1"})

    # Locate worktree.
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

    # ---- 3. commit work on the worktree branch ----
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

    # ---- 4. simulate HEAD wandering in the main repo ----
    _git(project, "checkout", "-b", "wandered-feature")

    # ---- 5. walk execute → document → review ----
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- 6. transition done MUST refuse ----
    # HATS-1682: `review -> done` is a consent point the role declares. The
    # answer is scaffolding here — what this test measures is what the
    # WORKTREE layer does with the edge once consent is in hand.
    res = rack("transition", task_id, "done", expect_exit=1, extra_env=consent_grant())
    combined = res.stdout + res.stderr

    # Positive: mismatch refusal surfaced with both branch names.
    assert "base branch mismatch" in combined.lower(), f"mismatch refusal not surfaced:\n{combined}"
    assert "wandered-feature" in combined, f"current branch name missing from refusal:\n{combined}"
    assert base_branch in combined, f"expected branch name missing from refusal:\n{combined}"

    # Positive: the ported recipe — `git checkout <expected>` + retry.
    assert f"git checkout {base_branch}" in combined, (
        f"recovery `git checkout {base_branch}` missing from recipe:\n{combined}"
    )
    assert f"rack transition {task_id} --state done" in combined, (
        f"retry step missing from recipe:\n{combined}"
    )
    assert str(project) in combined, f"main-repo path missing from the cd hint:\n{combined}"

    # ---- 7. critical safety: NO wrong-branch merge happened ----
    wandered_log = _git(project, "log", "--oneline", "wandered-feature").stdout
    assert "wt-work" not in wandered_log, (
        f"wandered branch MUST NOT receive the worktree commit "
        f"(this is the HATS-509 live incident shape):\n{wandered_log}"
    )
    base_log = _git(project, "log", "--oneline", base_branch).stdout
    assert "wt-work" not in base_log, (
        f"base branch unexpectedly received the worktree commit:\n{base_log}"
    )

    # ---- 8. card remains in `review` (HATS-481 fail-loud) ----
    show = rack("context", task_id)
    assert "state: review" in show.stdout, (
        f"task should remain in `review` after mismatch refusal:\n{show.stdout}"
    )

    # ---- 9. recovery: switch back, the transition succeeds ----
    _git(project, "checkout", base_branch)
    rack("transition", task_id, "done", extra_env=consent_grant())
    show2 = rack("context", task_id)
    assert "state: done" in show2.stdout, (
        f"task did not reach `done` after recovery:\n{show2.stdout}"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in history after recovery:\n{log}"
