"""e2e (HATS-1205)

flow:   a developer running commands via wt exec when a deleted worktree directory
        remains
cmds:
    # when a worktree directory exists on disk but git worktree prune was run
    ai-hats wt exec -- git rev-parse --abbrev-ref HEAD
expect: dead worktrees drop out of active selector resolution without causing ambiguity
        errors
why:    list_active must verify git liveness so pruned worktrees do not block execution
"""

from __future__ import annotations

import pytest

from _helpers.wt import ai_hats, child_env, git, two_worktrees, worktree_branches

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _kill_worktree(main, wt) -> None:
    """Reproduce the observed phantom: the directory survives, git forgets it."""
    (wt / ".git").unlink()
    git(main, "worktree", "prune")


def test_dead_worktree_drops_out_of_the_ambiguity_set(tmp_project, repo_root):
    main = tmp_project
    env = child_env(repo_root)
    dead_branch, dead_wt = two_worktrees(main.path, env)
    survivor = next(b for b in worktree_branches(main.path) if b != dead_branch)
    _kill_worktree(main.path, dead_wt)

    res = ai_hats(
        main.ai_hats_binary,
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

    assert res.returncode == 0, (
        f"🐛 HATS-1205: the phantom {dead_branch} still forces a selector:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert res.stdout.strip().splitlines()[-1] == survivor, (
        f"expected to land in the surviving worktree {survivor}, got {res.stdout!r}"
    )
    assert dead_wt.is_dir(), "the directory itself is left alone — only the claim is dropped"
