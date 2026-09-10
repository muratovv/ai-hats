"""e2e (HATS-1205)

flow:   a developer executing commands via wt exec from inside a worktree subdirectory
cmds:
    # from a subdirectory inside a worktree
    ai-hats wt exec -- git rev-parse --show-prefix
expect: command executes in the relative subdirectory within the worktree rather than
        teleporting to root
why:    wt exec must preserve caller relative path inside worktree directories
"""

from __future__ import annotations

import pytest

from _helpers.wt import ai_hats, child_env, last_line, two_worktrees

pytestmark = [pytest.mark.integration, pytest.mark.wt]

# textual + symlink-immune: on macOS /var/folders resolves to /private/var/folders,
# so comparing `pwd` output against a constructed path is flaky.
_PROBE = ("git", "rev-parse", "--show-prefix")


def test_wt_exec_runs_in_the_subdirectory_you_stand_in(tmp_project, repo_root):
    """S1: inside `<wt>/sub`, the bare form needs no selector and no flag."""
    main = tmp_project
    env = child_env(repo_root)
    _, wt = two_worktrees(main.path, env)
    sub = wt / "sub"
    sub.mkdir()

    res = ai_hats(main.ai_hats_binary, "wt", "exec", "--", *_PROBE, cwd=sub, env=env)

    assert res.returncode == 0, (
        "🐛 HATS-1205: the bare `wt exec` form must resolve from cwd inside a "
        f"worktree subdirectory:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert last_line(res) == "sub/", (
        "🐛 HATS-1205: `wt exec` teleported to the worktree root instead of "
        f"running where the caller stands. Expected 'sub/', got {res.stdout!r}"
    )


def test_wt_exec_from_outside_still_lands_at_the_worktree_root(tmp_project, repo_root):
    """R5 back-compat: every published example runs from the main checkout."""
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    (wt / "sub").mkdir()

    res = ai_hats(main.ai_hats_binary, "wt", "exec", branch, "--", *_PROBE, cwd=main.path, env=env)

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert last_line(res) == "", (
        "from outside the worktree the command must still start at the worktree "
        f"root, got prefix {last_line(res)!r}"
    )
