"""In-process guards for what ``wt exec`` hands the inner command.

HATS-887: an ambient GIT_DIR must not reach the subprocess env. HATS-1213: an
explicit selector is resolved instead of cwd, and never survives into argv.

Fast unit proofs — the e2e files are the gate tests, but they can only validate
GREEN at the main checkout (the dev-venv shim runs the installed package, not
worktree-local src), so the ordering contract is pinned here too.
"""

from __future__ import annotations

from unittest import mock

import click
import pytest
from click.testing import CliRunner

from ai_hats.cli.worktree import _peel_selector, wt


def _project_value(root):
    """A real Project anchored at the fixture dir — the factory is patched, the types are not."""
    from ai_hats.config.project import ProjectConfig
    from ai_hats.project import Project
    from ai_hats_core.layout import ProjectLayout

    layout = ProjectLayout.at(root)
    return Project(layout=layout, config=ProjectConfig(), venv=layout.default_venv, library_paths=())



def _active(*branches: str) -> list[mock.Mock]:
    return [mock.Mock(branch_name=b) for b in branches]


def test_wt_exec_strips_git_plumbing_from_subprocess_env(monkeypatch, tmp_path):
    """RED-under-revert: drop the GIT_* pop in ``wt_exec`` and the env keeps them."""
    monkeypatch.setenv("GIT_DIR", "/real/repo/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/real/repo")
    monkeypatch.setenv("GIT_INDEX_FILE", "/real/repo/.git/index")

    fake_mgr = mock.Mock()
    fake_mgr.worktree_path = tmp_path / "wt"
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return mock.Mock(returncode=0)

    # _peel_selector stubbed out: this test is about the env, and the real one
    # would need git for _project_dir (the subprocess.run patch below lands on
    # the stdlib module object, so it would answer that git call with a Mock).
    with (
        mock.patch("ai_hats.cli.worktree._peel_selector", return_value=None),
        mock.patch("ai_hats.cli.worktree._resolve_worktree", return_value=fake_mgr),
        mock.patch("ai_hats.cli.worktree.subprocess.run", side_effect=_fake_run),
    ):
        result = CliRunner().invoke(wt, ["exec", "--", "git", "rev-parse", "HEAD"])

    assert result.exit_code == 0, result.output
    env = captured["env"]
    assert env is not None, "wt exec must pass an explicit env"
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        assert var not in env, f"wt exec leaked {var} into the inner command env"
    # Sanity: the worktree src pin still rides through (fix didn't nuke the env).
    assert str(fake_mgr.worktree_path / "src") in env["PYTHONPATH"]


@pytest.mark.parametrize(
    "active",
    [("task/a", "task/b"), ("task/b",)],
    ids=["two-active", "sole-active"],
)
def test_wt_exec_resolves_the_selector_not_cwd(tmp_path, active):
    """HATS-1213 ordering: the selector reaches resolution and leaves argv.

    Both cases used to skip the peel — ``_resolve_worktree()`` returned without
    raising (cwd, or the sole-active convenience), so the branch name stayed in
    the command vector and was exec'd as the program.
    """
    fake_mgr = mock.Mock()
    fake_mgr.worktree_path = tmp_path / "wt"
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return mock.Mock(returncode=0)

    with (
        mock.patch("ai_hats.cli._entry.resolve_project", return_value=_project_value(tmp_path)),
        mock.patch("ai_hats_wt.WorktreeManager.list_active", return_value=_active(*active)),
        mock.patch("ai_hats.cli.worktree._resolve_worktree", return_value=fake_mgr) as resolve,
        mock.patch("ai_hats.cli.worktree.subprocess.run", side_effect=_fake_run),
    ):
        result = CliRunner().invoke(wt, ["exec", "task/b", "--", "git", "status"])

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with("task/b")
    assert captured["cmd"] == ["git", "status"], (
        f"selector or separator leaked into argv: {captured['cmd']!r}"
    )


@pytest.mark.parametrize("argv,expected", [(["env"], None), (["env", "task/a"], "task/a")])
def test_wt_env_honours_an_optional_branch(tmp_path, argv, expected):
    """HATS-1213: `wt env <branch>` reaches in; the bare form still infers."""
    fake_mgr = mock.Mock()
    fake_mgr.worktree_path = tmp_path / "wt"

    with mock.patch("ai_hats.cli.worktree._resolve_worktree", return_value=fake_mgr) as resolve:
        result = CliRunner().invoke(wt, argv)

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with(expected)
    assert f'export WT="{fake_mgr.worktree_path}"' in result.output


def test_peel_selector_pops_the_branch_and_a_trailing_separator(tmp_path):
    args = ["task/a", "--", "pytest", "-x"]
    with (
        mock.patch("ai_hats.cli._entry.resolve_project", return_value=_project_value(tmp_path)),
        mock.patch("ai_hats_wt.WorktreeManager.list_active", return_value=_active("task/a")),
    ):
        assert _peel_selector(args) == "task/a"
    assert args == ["pytest", "-x"]


def test_peel_selector_leaves_a_plain_command_alone(tmp_path):
    """A first arg that names no active worktree is the command, not a selector."""
    args = ["pytest", "-x"]
    with (
        mock.patch("ai_hats.cli._entry.resolve_project", return_value=_project_value(tmp_path)),
        mock.patch("ai_hats_wt.WorktreeManager.list_active", return_value=_active("task/a")),
    ):
        assert _peel_selector(args) is None
    assert args == ["pytest", "-x"], "a non-selector first arg must survive untouched"


def test_peel_selector_refuses_a_selector_with_no_command(tmp_path):
    args = ["task/a"]
    with (
        mock.patch("ai_hats.cli._entry.resolve_project", return_value=_project_value(tmp_path)),
        mock.patch("ai_hats_wt.WorktreeManager.list_active", return_value=_active("task/a")),
        pytest.raises(click.UsageError, match="No command to run"),
    ):
        _peel_selector(args)
