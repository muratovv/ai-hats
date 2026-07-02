"""HATS-887 — in-process guard for the ``wt exec`` GIT_* strip.

Fast unit proof (the e2e ``test_wt_exec_git_env_isolation`` is the gate test, but
it can only validate GREEN at the main checkout — the dev-venv shim runs the
installed package, not worktree-local src). Here we drive the worktree code
directly: an ambient GIT_DIR must NOT reach the subprocess env ``wt exec`` builds.
RED-under-revert: drop the GIT_* pop in ``wt_exec`` and the captured env keeps them.
"""
from __future__ import annotations

from unittest import mock

from click.testing import CliRunner

from ai_hats.cli.worktree import wt


def test_wt_exec_strips_git_plumbing_from_subprocess_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", "/real/repo/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/real/repo")
    monkeypatch.setenv("GIT_INDEX_FILE", "/real/repo/.git/index")

    fake_mgr = mock.Mock()
    fake_mgr.worktree_path = tmp_path / "wt"
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return mock.Mock(returncode=0)

    with mock.patch("ai_hats.cli.worktree._resolve_worktree", return_value=fake_mgr), \
         mock.patch("ai_hats.cli.worktree.subprocess.run", side_effect=_fake_run):
        result = CliRunner().invoke(wt, ["exec", "--", "git", "rev-parse", "HEAD"])

    assert result.exit_code == 0, result.output
    env = captured["env"]
    assert env is not None, "wt exec must pass an explicit env"
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        assert var not in env, f"wt exec leaked {var} into the inner command env"
    # Sanity: the worktree src pin still rides through (fix didn't nuke the env).
    assert str(fake_mgr.worktree_path / "src") in env["PYTHONPATH"]
