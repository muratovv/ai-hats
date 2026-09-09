"""e2e (HATS-1205)

flow:   a developer executing python commands via wt exec in a subproject with its own
        pyproject.toml
cmds:
    # from inside a subproject directory with pyproject.toml
    ai-hats wt exec -C sub -- python -c "import mypkg"
expect: PYTHONPATH is rooted at the owning subproject directory containing
        pyproject.toml
why:    subprojects must resolve their own src packages without mixing outer repository
        packages
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from _helpers.wt import ai_hats, child_env, two_worktrees

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _seed(wt: Path) -> None:
    """`sub/` owns itself (pyproject + src); `plain/` is a bare directory."""
    inner = wt / "sub" / "src" / "mypkg"
    inner.mkdir(parents=True)
    (inner / "__init__.py").write_text("")
    (wt / "sub" / "pyproject.toml").write_text('[project]\nname = "sub"\nversion = "0"\n')
    outer = wt / "packages" / "outerpkg" / "src" / "outerpkg"
    outer.mkdir(parents=True)
    (outer / "__init__.py").write_text("")
    (wt / "plain").mkdir()


def test_subproject_gets_its_own_src(tmp_project, repo_root):
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    _seed(wt)

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        "-C",
        "sub",
        "--",
        sys.executable,
        "-c",
        "import mypkg; print(mypkg.__file__)",
        cwd=main.path,
        env=env,
    )

    assert res.returncode == 0, (
        "🐛 HATS-1205: PYTHONPATH was not re-rooted at the subproject — its own "
        f"src is invisible:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    resolved = Path(res.stdout.strip().splitlines()[-1]).resolve()
    assert resolved.is_relative_to((wt / "sub").resolve()), (
        f"mypkg must resolve from the subproject, got {resolved}"
    )


def test_subproject_does_not_inherit_the_outer_workspace(tmp_project, repo_root):
    """The no-Franken-mix half: re-rooting REPLACES the outer roots."""
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    _seed(wt)

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        "-C",
        "sub",
        "--",
        sys.executable,
        "-c",
        "import outerpkg",
        cwd=main.path,
        env=env,
    )

    assert res.returncode != 0, (
        "a subproject that owns its dependency set must not see the outer "
        f"repo's packages:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )


def test_plain_subdirectory_keeps_the_worktree_root_env(tmp_project, repo_root):
    """A directory without a pyproject.toml is not a checkout root — the outer
    workspace must stay on PYTHONPATH (no silent loss for `-C tests`)."""
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    _seed(wt)

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        "-C",
        "plain",
        "--",
        sys.executable,
        "-c",
        "import outerpkg; print(outerpkg.__file__)",
        cwd=main.path,
        env=env,
    )

    assert res.returncode == 0, (
        "🐛 HATS-1205: a plain subdirectory lost the worktree's workspace "
        f"PYTHONPATH:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
