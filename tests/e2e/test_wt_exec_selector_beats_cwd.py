"""e2e (HATS-1213)

flow:   a developer executing wt exec with an explicit branch selector while standing
        in another worktree
cmds:
    # from inside worktree A
    ai-hats wt exec task/hats-b -- git rev-parse --abbrev-ref HEAD
expect: explicit branch selector takes precedence over caller current working directory
why:    explicit branch arguments in wt exec must override implicit directory context
"""

from __future__ import annotations

import pytest

from _helpers.wt import (
    ai_hats,
    child_env,
    init_repo,
    last_line,
    spawn_worktree,
    two_worktrees,
    worktree_branches,
)

pytestmark = [pytest.mark.integration, pytest.mark.wt]

_HEAD = ("git", "rev-parse", "--abbrev-ref", "HEAD")


def _two(main, repo_root):
    """(env, branch_a, path_a, branch_b, path_b) for two managed worktrees."""
    env = child_env(repo_root)
    branch_a, wt_a = two_worktrees(main.path, env)
    branches = worktree_branches(main.path)
    branch_b = sorted(branches)[1]
    return env, branch_a, wt_a, branch_b, branches[branch_b]


@pytest.mark.parametrize("sep", [("--",), ()], ids=["with-dashdash", "bare"])
def test_selector_beats_cwd_from_inside_another_worktree(tmp_project, repo_root, sep):
    """The card's repro: standing in A, `wt exec B` must run in B, not A."""
    main = tmp_project
    env, branch_a, wt_a, branch_b, _ = _two(main, repo_root)

    res = ai_hats(main.ai_hats_binary, "wt", "exec", branch_b, *sep, *_HEAD, cwd=wt_a, env=env)

    assert res.returncode == 0, (
        f"🐛 HATS-1213: `wt exec {branch_b}` from inside {branch_a} failed instead of "
        f"routing (rc={res.returncode}):\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert last_line(res) == branch_b, (
        f"selector `{branch_b}` was ignored in favour of cwd ({branch_a}): {res.stdout!r}"
    )


def test_selector_with_cd_from_inside_another_worktree(tmp_project, repo_root):
    """The misleading shape: `-C` named a subdir that exists only in the target.

    Before the fix the resolver picked cwd's worktree, where `sub/` is absent, so
    the refusal read as "my subdir is missing" rather than "your selector was
    ignored" — this is how the bug was found during HATS-1205 acceptance.
    """
    main = tmp_project
    env, branch_a, wt_a, branch_b, wt_b = _two(main, repo_root)
    (wt_b / "sub").mkdir()

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch_b,
        "-C",
        "sub",
        "--",
        "git",
        "rev-parse",
        "--show-prefix",
        cwd=wt_a,
        env=env,
    )

    assert res.returncode == 0, (
        f"🐛 HATS-1213: `-C sub` resolved against cwd ({branch_a}) instead of "
        f"{branch_b}:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert last_line(res) == "sub/", f"did not land in {branch_b}/sub: {res.stdout!r}"


def test_selector_beats_the_sole_active_worktree(tmp_project, repo_root):
    """R3: `len(active) == 1` resolved without raising, so the peel never ran and
    the branch name was handed to subprocess as the program."""
    main = tmp_project
    env = child_env(repo_root)
    init_repo(main.path)
    spawn_worktree(main.path, "HATS-1", env)
    branch = next(iter(worktree_branches(main.path)))

    res = ai_hats(main.ai_hats_binary, "wt", "exec", branch, "--", *_HEAD, cwd=main.path, env=env)

    assert res.returncode == 0, (
        f"🐛 HATS-1213: sole-worktree selector run as the command "
        f"(rc={res.returncode}):\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert last_line(res) == branch, f"selector `{branch}` mis-routed: {res.stdout!r}"


def test_selector_targeting_your_own_worktree_is_accepted(tmp_project, repo_root):
    """Naming the worktree you are already standing in is a no-op, not a refusal —
    and must not leave the selector in the command vector."""
    main = tmp_project
    env, branch_a, wt_a, _, _ = _two(main, repo_root)

    res = ai_hats(main.ai_hats_binary, "wt", "exec", branch_a, "--", *_HEAD, cwd=wt_a, env=env)

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert last_line(res) == branch_a, f"expected {branch_a}, got {res.stdout!r}"
