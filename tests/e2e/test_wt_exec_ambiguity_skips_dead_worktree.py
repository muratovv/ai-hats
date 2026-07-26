"""e2e (HATS-1205): a worktree git no longer knows about must drop out of the
selector-ambiguity set.

``list_active`` claimed to prune stale entries but only dropped ones whose state
JSON had vanished — a directory still on disk without a ``.git`` file survived.
Observed: ``wt exec`` demanded a selector among 6 branches where 4 were real.

Scope note: ``wt list`` was never affected — it enumerates git's own worktrees
(and marks the ai-hats-tracked ones), so a pruned worktree is already absent
there. Only the ``list_active`` consumers carried the phantom.

Fail-under-revert: drop the liveness filter in ``list_active`` and the bare
``wt exec`` form goes back to refusing with an ambiguity error naming the dead
branch.
"""
from __future__ import annotations

import pytest

from _helpers.wt import ai_hats, child_env, git, two_worktrees, worktree_branches

pytestmark = pytest.mark.integration


def _kill_worktree(main, wt) -> None:
    """Reproduce the observed phantom: the directory survives, git forgets it."""
    (wt / ".git").unlink()
    git(main, "worktree", "prune")


def test_dead_worktree_drops_out_of_the_ambiguity_set(tmp_project, repo_root):
    main = tmp_project
    env = child_env(repo_root)
    dead_branch, dead_wt = two_worktrees(main.ai_hats_binary, main.path, env)
    survivor = next(b for b in worktree_branches(main.path) if b != dead_branch)
    _kill_worktree(main.path, dead_wt)

    res = ai_hats(
        main.ai_hats_binary, "wt", "exec", "--", "git", "rev-parse", "--abbrev-ref", "HEAD",
        cwd=main.path, env=env,
    )

    assert res.returncode == 0, (
        f"🐛 HATS-1205: the phantom {dead_branch} still forces a selector:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert res.stdout.strip().splitlines()[-1] == survivor, (
        f"expected to land in the surviving worktree {survivor}, got {res.stdout!r}"
    )
    assert dead_wt.is_dir(), "the directory itself is left alone — only the claim is dropped"
