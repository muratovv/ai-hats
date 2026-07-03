"""Unit contract of the GIT_* plumbing scrub (moved to core in HATS-862)."""

from __future__ import annotations

from ai_hats_core import scrubbed_git_env


def test_strips_plumbing_vars_keeps_identity(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/tmp/wrong/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/wrong")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/wrong/index")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "keeper")

    env = scrubbed_git_env()

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert env["GIT_AUTHOR_NAME"] == "keeper"


def test_noop_without_plumbing_vars(monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)

    env = scrubbed_git_env()

    assert "GIT_DIR" not in env
    assert env  # a real environ copy, not empty
