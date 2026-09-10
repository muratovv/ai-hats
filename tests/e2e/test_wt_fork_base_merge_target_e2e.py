"""e2e (HATS-942)

flow:   a developer creating and merging task worktrees in a fork repository setup
cmds:
    # in a repo with base_branch=main and merge_target=fork-main
    rack transition HATS-001 execute
    rack transition HATS-001 done
expect: worktree is cut from base_branch and changes are merged into merge_target on
        done
why:    fork repository setups require cutting from base branch while landing work on
        merge target
"""

from __future__ import annotations
from _helpers.git import git as _git

from dataclasses import replace
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV


pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _rack_driver(proj):
    """Same project + env, driven through the `rack` console script that the
    shared venv installs alongside the launcher (HATS-1263)."""
    return replace(proj, ai_hats_binary=Path(proj.env[ENV_AI_HATS_VENV]) / "bin" / "rack")


def _task_worktree_path(project: Path) -> Path:
    """The single linked (non-main) worktree path via `git worktree list`."""
    out = _git(project, "worktree", "list", "--porcelain").stdout
    paths = [
        Path(line.split(" ", 1)[1].strip())
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]
    linked = [p for p in paths if p.resolve() != project.resolve()]
    assert len(linked) == 1, f"expected exactly one linked worktree; got {linked}"
    return linked[0]


def _fill_plan(project: Path, task_id: str) -> None:
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    assert plan_path.exists(), f"expected plan scaffold at {plan_path}"
    plan_path.write_text(
        "# Plan\n\n"
        "## Requirements\nprobe\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )


def _setup_fork_repo(project: Path) -> None:
    """`main` = upstream mirror (README + ai-hats.yaml); `fork-main` = dev trunk
    one commit ahead (FORK.md). Configures worktree.base_branch/merge_target."""
    from ai_hats.models import ProjectConfig, WorktreeConfig
    from ai_hats.paths import PROJECT_CONFIG

    _git(project, "checkout", "-b", "main")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    (project / ".agent").mkdir(exist_ok=True)
    (project / ".agent" / "STATE.md").write_text("")
    ProjectConfig(
        provider="claude",
        library_paths=[],
        worktree=WorktreeConfig(base_branch="main", merge_target="fork-main"),
    ).save(project / PROJECT_CONFIG)
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init (main)")

    _git(project, "checkout", "-b", "fork-main")
    (project / "FORK.md").write_text("fork trunk only\n")
    _git(project, "add", "FORK.md")
    _git(project, "commit", "-m", "fork trunk work")


def test_fork_base_merge_target_lifecycle(tmp_venv_project) -> None:
    proj = tmp_venv_project
    project = proj.path
    rack = _rack_driver(proj)

    _setup_fork_repo(project)
    # Operator sits on the merge target for the whole lifecycle.
    _git(project, "checkout", "fork-main")

    # --- Seed a task and walk it to execute on fork-main ---
    # Id prefix is rack's default (HATS): the fixture config declares no task_prefix.
    task_id = "HATS-001"
    rack.run("create", "Probe HATS-942 fork e2e").expect_ok()
    rack.run("transition", task_id, "plan").expect_ok()
    _fill_plan(project, task_id)

    # execute is ACCEPTED on fork-main (default guard would refuse non-canonical).
    rack.run("transition", task_id, "execute").expect_ok()

    wt = _task_worktree_path(project)
    # Cut from `main` (base_branch): the worktree must NOT carry fork-main's file.
    assert not (wt / "FORK.md").exists(), (
        "worktree should be cut from `main` (no FORK.md), not from HEAD=fork-main"
    )
    assert (wt / "README.md").exists()

    # --- Do work in the worktree, then land it via the FSM walk to `done` ---
    (wt / "TASKWORK.md").write_text("task output\n")
    _git(wt, "add", "TASKWORK.md")
    _git(wt, "commit", "-m", "task work")

    # FSM: execute → document → review → done. Teardown (merge into fork-main)
    # fires on `→ done`. HEAD stays on fork-main throughout (HATS-533 satisfied).
    rack.run("transition", task_id, "document").expect_ok()
    rack.run("transition", task_id, "review").expect_ok()
    rack.run("transition", task_id, "done").expect_ok()

    # Merge landed on fork-main; main is the untouched upstream mirror.
    fork_files = _git(project, "ls-tree", "-r", "--name-only", "fork-main").stdout
    main_files = _git(project, "ls-tree", "-r", "--name-only", "main").stdout
    assert "TASKWORK.md" in fork_files, "work must land on fork-main"
    assert "TASKWORK.md" not in main_files, "main (upstream mirror) must stay untouched"
    assert "FORK.md" not in main_files, "sanity: FORK.md never reached main"


def test_wt_create_refuses_when_head_off_merge_target(tmp_venv_project) -> None:
    """Guard names the configured merge target. HEAD sits on `main` (a canonical
    base!), but `merge_target: fork-main` narrows the guard — `wt create` refuses
    and the message names `fork-main`.

    Fail-under-revert: revert the guard generalization → `main` is canonical →
    `wt create` succeeds (exit 0) → ``expect_failure`` fails.
    """
    proj = tmp_venv_project
    project = proj.path

    _setup_fork_repo(project)
    _git(project, "checkout", "main")  # canonical HEAD, but target is fork-main

    (
        proj.run("wt", "create", "task/probe")
        .expect_failure()
        .expect_stdout_contains("Refused", "fork-main")
    )

    # No worktree leak.
    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 1, f"refusal must not create a worktree; got {wt_list}"
