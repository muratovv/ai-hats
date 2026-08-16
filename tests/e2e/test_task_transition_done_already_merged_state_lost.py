"""e2e (HATS-596, HATS-697, HATS-1263)

flow:   a developer finalizing a task whose branch was manually merged and worktree
        state metadata was removed
cmds:
    # when task branch is merged into base and worktree state file is deleted
    rack transition TST-001 done
expect: transition to done completes successfully and cleans up the merged branch
        without raising state lost errors
why:    tasks with merged branches must finalize cleanly even if worktree state tracking
        files have been removed
"""

from __future__ import annotations
from _helpers.env import consent_grant
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest


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
def test_e2e_transition_done_already_merged_state_lost(shared_launcher, tmp_path):
    """HATS-697 on the `rack transition done` surface.

    Scenario (mirrors the PROX-287 incident):
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk brainstorm → plan → execute (worktree from base).
      4. Commit work on the worktree branch.
      5. Merge the branch into base in the main repo (`--no-ff`) — work is
         now fully integrated; branch is an ancestor of base.
      6. Remove the auto-worktree by hand AND delete its ai-hats state JSON
         so `load_for_task` resolves to None (the lost-state condition).
      7. Walk execute → document → review.
      8. ``rack transition <ID> done`` MUST exit 0 (finalize without
         re-merge — NOT a false `worktree state lost` refusal).
      9. Task reaches ``done``; the now-merged branch is cleaned up.
     10. No double-merge: base ref unchanged since the manual merge.
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

    # ---- 1. bootstrap ----
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

    # ---- 2. create task → execute (worktree from base) ----
    new_res = rack(
        "create",
        "already merged state-lost test",
        "--description",
        "exercise the HATS-697 state-lost finalize",
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
        "# Plan\n\n## Requirements\nexercise state-lost finalize.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    # plan → execute is consent-gated on rack; the launcher env carries no ack.
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
    task_branch = f"task/{task_id.lower()}"

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

    # ---- 4. INCIDENT setup: merge branch into base (work integrated) ----
    _git(
        project,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "merge",
        "--no-ff",
        "--no-edit",
        task_branch,
    )
    base_sha_after_merge = _git(project, "rev-parse", base_branch).stdout.strip()

    # ---- 5. lose the worktree state: remove the worktree by hand AND
    #         delete its ai-hats state JSON so load_for_task → None. ----
    _git(project, "worktree", "remove", "--force", str(wt_path))
    assert not wt_path.exists(), f"worktree dir not removed: {wt_path}"
    state_dir = project / ".agent" / "ai-hats" / "sessions" / "worktrees"
    removed_state = False
    for state_json in state_dir.glob(f"task-{task_id.lower()}*.json"):
        state_json.unlink()
        removed_state = True
    assert removed_state, (
        f"no worktree state JSON found to delete under {state_dir}:\n"
        f"{[p.name for p in state_dir.glob('*')] if state_dir.is_dir() else 'dir absent'}"
    )

    # ---- 6. walk execute → document → review ----
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- 7. transition done MUST SUCCEED (the fix) ----
    # HATS-1682: `review -> done` is a consent point the role declares. The
    # answer is scaffolding here — what this test measures is what the
    # WORKTREE layer does with the edge once consent is in hand.
    res = rack("transition", task_id, "done", expect_exit=0, extra_env=consent_grant())
    combined = res.stdout + res.stderr
    assert "worktree state lost" not in combined.lower(), (
        f"false state-lost refusal — HATS-697 short-circuit not applied:\n{combined}"
    )

    # ---- 8. task done; merged branch cleaned up ----
    show = rack("context", task_id)
    assert "state: done" in show.stdout, f"task did not reach `done`:\n{show.stdout}"
    branches = _git(project, "branch", "--list", task_branch).stdout.strip()
    assert branches == "", f"already-merged branch not cleaned up by finalize: {branches!r}"

    # ---- 9. no double-merge: base ref unchanged ----
    assert _git(project, "rev-parse", base_branch).stdout.strip() == base_sha_after_merge, (
        "base branch was re-merged — finalize should NOT run git merge"
    )
