"""e2e (HATS-1205): ``ai-hats wt exec <branch> -C <subdir>`` reaches into a
worktree subdirectory from OUTSIDE the worktree — the S2 scenario, where cwd
carries no signal.

Fail-under-revert: drop the ``-C`` plumbing in ``wt_exec`` and the inner
``git rev-parse --show-prefix`` reports ``""`` (the worktree root), not ``sub/``.
"""

from __future__ import annotations

import pytest

from _helpers.wt import ai_hats, child_env, last_line, two_worktrees

pytestmark = pytest.mark.integration

_PROBE = ("git", "rev-parse", "--show-prefix")


@pytest.mark.parametrize("flag", ["-C", "--cd"])
def test_wt_exec_cd_reaches_a_subdir_from_the_main_checkout(tmp_project, repo_root, flag):
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    (wt / "sub").mkdir()

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        flag,
        "sub",
        "--",
        *_PROBE,
        cwd=main.path,
        env=env,
    )

    assert res.returncode == 0, (
        f"🐛 HATS-1205: `wt exec {branch} {flag} sub` failed:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert last_line(res) == "sub/", (
        f"`{flag} sub` did not land in the subdirectory: got {res.stdout!r}"
    )


def test_wt_exec_cd_overrides_cwd(tmp_project, repo_root):
    """Precedence: an explicit -C wins over the caller's cwd."""
    main = tmp_project
    env = child_env(repo_root)
    _, wt = two_worktrees(main.path, env)
    (wt / "sub").mkdir()
    other = wt / "other"
    other.mkdir()

    res = ai_hats(main.ai_hats_binary, "wt", "exec", "-C", "sub", "--", *_PROBE, cwd=other, env=env)

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert last_line(res) == "sub/", f"-C must override cwd ({other.name}/), got {res.stdout!r}"


def test_wt_exec_cd_nonexistent_target_fails_cleanly(tmp_project, repo_root):
    """A typo'd target is a refusal with a readable message, not a traceback."""
    main = tmp_project
    env = child_env(repo_root)
    branch, _ = two_worktrees(main.path, env)

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        "-C",
        "nope",
        "--",
        *_PROBE,
        cwd=main.path,
        env=env,
    )

    assert res.returncode != 0, "a nonexistent -C target must refuse"
    combined = res.stdout + res.stderr
    assert "Traceback" not in combined, f"leaked a traceback:\n{combined}"
    assert "nope" in combined, f"the refusal must name the bad target:\n{combined}"
