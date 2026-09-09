"""e2e (HATS-859)

flow:   a developer executing commands in a specific worktree using a branch selector
cmds:
    ai-hats wt exec task/hats-1 -- git rev-parse --abbrev-ref HEAD
expect: the command routes to the specified worktree branch when multiple worktrees
        exist
why:    wt exec requires explicit branch selector to route commands when multiple
        worktrees are active"""

from __future__ import annotations

import pytest

from _helpers.wt import (
    ai_hats as _ai_hats,
    child_env as _child_env,
    init_repo as _init_repo,
    spawn_worktree as _spawn_worktree,
    worktree_branches as _worktree_branches,
)

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def test_wt_exec_selector_routes_to_named_worktree(tmp_project, repo_root):
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    # Two managed worktrees → _resolve_worktree() with no selector is ambiguous.
    _spawn_worktree(main.path, "HATS-1", env)
    _spawn_worktree(main.path, "HATS-2", env)
    branches = _worktree_branches(main.path)
    assert len(branches) >= 2, f"expected two linked worktrees, got {branches}"
    picks = sorted(branches)[:2]

    # A leading selector that names an active worktree must route there — both
    # with and without the `--` separator. The inner `git rev-parse` reports the
    # branch of whatever worktree it actually ran in.
    for branch in picks:
        with_dd = _ai_hats(
            binary,
            "wt",
            "exec",
            branch,
            "--",
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=main.path,
            env=env,
        )
        assert with_dd.returncode == 0, (
            f"🐛 HATS-859 REGRESSION: `wt exec {branch} -- …` failed instead of routing:\n"
            f"stdout:\n{with_dd.stdout}\nstderr:\n{with_dd.stderr}"
        )
        assert with_dd.stdout.strip().splitlines()[-1] == branch, (
            f"selector `{branch}` ran in the wrong worktree: got {with_dd.stdout!r}"
        )

        no_dd = _ai_hats(
            binary,
            "wt",
            "exec",
            branch,
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=main.path,
            env=env,
        )
        assert no_dd.returncode == 0 and no_dd.stdout.strip().splitlines()[-1] == branch, (
            f"selector `{branch}` without `--` mis-routed: rc={no_dd.returncode} "
            f"stdout={no_dd.stdout!r} stderr={no_dd.stderr!r}"
        )


def test_wt_exec_without_selector_still_reports_ambiguity(tmp_project, repo_root):
    """R2 contract: with >1 worktree and no selector, the command must still
    refuse with the actionable ambiguity error (not silently pick one)."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(main.path, "HATS-1", env)
    _spawn_worktree(main.path, "HATS-2", env)

    res = _ai_hats(
        binary,
        "wt",
        "exec",
        "--",
        "git",
        "rev-parse",
        "--abbrev-ref",
        "HEAD",
        cwd=main.path,
        env=env,
    )
    assert res.returncode != 0, "no-selector form must refuse when >1 worktree active"
    assert "Multiple active worktrees" in (res.stderr + res.stdout)
