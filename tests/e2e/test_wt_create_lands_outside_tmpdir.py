"""e2e (HATS-1632)

flow:   an agent taking a card into execute, which mints the worktree
cmds:
    rack transition <ID> execute      # the FSM road, via wt_effects
    ai-hats wt merge <branch>         # teardown from the new root
expect: the tree lands under <cache_home>/<project-key>/worktrees/, never in the
        temp root, and merge still tears it down leaving no admin entry
why:    macOS reaps $TMPDIR by access time (dirhelper, 3 days) and a
        uv-materialized venv arrives pre-aged from the uv cache, so a worktree
        born there loses its cold half at the next sweep
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _helpers.wt import ai_hats, child_env, git, init_repo, spawn_worktree, worktree_branches

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _cache_pinned_env(repo_root: Path, cache_home: Path) -> dict[str, str]:
    env = child_env(repo_root)
    env["AI_HATS_CACHE_HOME"] = str(cache_home)
    return env


def test_execute_mints_the_worktree_under_the_cache_root(tmp_project, repo_root, tmp_path):
    """The tree is born outside the temp root — the whole point of the card."""
    main = tmp_project
    cache_home = tmp_path / "_wt_cache_home"
    env = _cache_pinned_env(repo_root, cache_home)
    init_repo(main.path)

    spawn_worktree(main.path, "HATS-1", env)

    branches = worktree_branches(main.path)
    assert branches, "the execute transition minted no linked worktree"
    wt_path = next(iter(branches.values())).resolve()
    assert wt_path.is_relative_to(cache_home.resolve()), (
        f"🐛 HATS-1632: worktree must be minted under the cache root "
        f"{cache_home}, got {wt_path} — a tree in $TMPDIR is reaped by atime"
    )
    assert wt_path.parent.name == "worktrees", (
        f"expected <cache_root>/worktrees/<tree>, got parent {wt_path.parent}"
    )


def test_merge_tears_down_a_worktree_from_the_new_root(tmp_project, repo_root, tmp_path):
    """Relocating the root must not strand the tree or its git admin entry."""
    main = tmp_project
    cache_home = tmp_path / "_wt_cache_home"
    env = _cache_pinned_env(repo_root, cache_home)
    init_repo(main.path)
    spawn_worktree(main.path, "HATS-1", env)
    branch, wt_path = next(iter(worktree_branches(main.path).items()))
    (wt_path / "landed.txt").write_text("work from the relocated worktree\n")
    git(wt_path, "add", "landed.txt")
    git(wt_path, "commit", "-m", "wt: add landed.txt")

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "merge",
        branch,
        cwd=main.path,
        env={**env, "AI_HATS_MERGE_ACK": "1"},
    )

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert (main.path / "landed.txt").exists(), "the commit did not land on the base branch"
    assert not wt_path.exists(), "the worktree directory survived its merge"
    assert not worktree_branches(main.path), (
        "git still tracks a linked worktree — a stranded admin entry blocks the "
        "next create on that branch (manager.py R-04 refuses to auto-prune)"
    )
