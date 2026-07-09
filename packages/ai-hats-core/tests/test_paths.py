"""HATS-952 — the shared worktree-free project-root resolver (ai_hats_core.paths).

The tracker and observe standalone CLIs delegate their ``_seam`` project-dir
default here instead of each carrying a copy (the walk-up was triplicated with
the integrator's richer wt-coupled ``_project_dir``).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core.paths import default_project_dir


def test_prefers_agent_holder(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".agent").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert default_project_dir() == tmp_path


def test_falls_back_to_git_dir_holder(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "x"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert default_project_dir() == tmp_path


def test_gitlink_file_holder(tmp_path: Path, monkeypatch) -> None:
    # A gitlink (linked worktree / submodule): the holder is returned wt-free,
    # NO hop to the main checkout (that hop is the integrator's job).
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    monkeypatch.chdir(tmp_path)
    assert default_project_dir() == tmp_path


def test_agent_wins_over_git(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    inner = tmp_path / "inner"
    (inner / ".agent").mkdir(parents=True)
    monkeypatch.chdir(inner)
    assert default_project_dir() == inner


def test_exported_from_package_root() -> None:
    import ai_hats_core

    assert ai_hats_core.default_project_dir is default_project_dir
