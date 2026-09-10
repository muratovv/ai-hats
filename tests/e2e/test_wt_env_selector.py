"""e2e (HATS-894)

flow:   a developer requesting environment variables for active worktrees
cmds:
    ai-hats wt env task/hats-1
expect: the command exports WT path for the named worktree or refuses when selector
        is omitted with multiple active worktrees
why:    wt env requires explicit branch selection when multiple worktrees are active"""

from __future__ import annotations

from pathlib import Path
import re
import pytest

from _helpers.wt import (
    ai_hats as _ai_hats,
    child_env as _child_env,
    init_repo as _init_repo,
    spawn_worktree as _spawn_worktree,
    worktree_branches as _worktree_branches,
)

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def test_wt_env_selector_routes_to_named_worktree(tmp_project, repo_root):
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(main.path, "HATS-1", env)
    _spawn_worktree(main.path, "HATS-2", env)
    branches = _worktree_branches(main.path)
    assert len(branches) >= 2, f"expected at least two linked worktrees, got {branches}"

    for branch in sorted(branches)[:2]:
        res = _ai_hats(
            binary,
            "wt",
            "env",
            branch,
            cwd=main.path,
            env=env,
        )
        assert res.returncode == 0, (
            f"🐛 HATS-894: `wt env {branch}` failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        match = re.search(r'export WT="([^"]+)"', res.stdout)
        assert match is not None, f"expected export WT line in stdout: {res.stdout!r}"
        wt_path = match.group(1)
        slug = branch.lower().replace("/", "-")
        assert slug in wt_path.lower() and Path(wt_path).is_dir(), (
            f"selector `{branch}` exported unexpected WT path: {wt_path!r}"
        )


def test_wt_env_without_selector_reports_ambiguity_when_multiple(tmp_project, repo_root):
    """R2 contract: with >1 worktree and no selector, wt env must refuse with ambiguity error."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(main.path, "HATS-1", env)
    _spawn_worktree(main.path, "HATS-2", env)

    res = _ai_hats(
        binary,
        "wt",
        "env",
        cwd=main.path,
        env=env,
    )
    assert res.returncode != 0, "no-selector form must refuse when >1 worktree active"
    assert "Multiple active worktrees" in (res.stderr + res.stdout)


def test_wt_env_without_selector_succeeds_with_single_worktree(tmp_project, repo_root):
    """With 1 active worktree, bare wt env succeeds and exports that worktree."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(main.path, "HATS-1", env)

    res = _ai_hats(
        binary,
        "wt",
        "env",
        cwd=main.path,
        env=env,
    )
    assert res.returncode == 0, f"bare wt env failed with single worktree: {res.stderr}"
    assert 'export WT="' in res.stdout
