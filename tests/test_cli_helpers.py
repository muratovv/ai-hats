"""Tests for CLI helpers — project-root resolution (HATS-197).

The walk-up logic must find the nearest ancestor with `.agent/` (or
`.git/`) so commands run from a subdirectory don't materialize a stray
`.agent/` next to CWD and split the backlog DB across two places.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.cli._helpers import _project_dir


@pytest.fixture
def repo_with_agent(tmp_path: Path) -> Path:
    """Project root with `.agent/` already initialized."""
    root = tmp_path / "repo"
    (root / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    return root


def test_project_dir_at_root(monkeypatch, repo_with_agent: Path) -> None:
    monkeypatch.chdir(repo_with_agent)
    assert _project_dir() == repo_with_agent


def test_project_dir_from_nested_subdir(monkeypatch, repo_with_agent: Path) -> None:
    """The bug: cwd is N levels deep, must walk up to the `.agent/` parent."""
    nested = repo_with_agent / "ansible" / "roles" / "monitoring"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    resolved = _project_dir()

    assert resolved == repo_with_agent
    # Cardinal sin guard: walking up must NOT have created a stray `.agent/`.
    assert not (nested / ".agent").exists()


def test_project_dir_prefers_agent_over_git(monkeypatch, tmp_path: Path) -> None:
    """A linked git worktree may carry its own `.git` file but no `.agent/`.
    The closest `.agent/` (in the main repo) must win over a closer `.git/`.
    """
    main = tmp_path / "main"
    (main / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (main / ".git").mkdir()

    # Simulate a linked-worktree-shaped layout INSIDE the main repo:
    # closer `.git` (file), but the nearest `.agent/` is still at `main/`.
    linked = main / "subproj"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: /elsewhere\n")  # worktree marker file

    monkeypatch.chdir(linked)
    assert _project_dir() == main


def test_project_dir_falls_back_to_git_root(monkeypatch, tmp_path: Path) -> None:
    """No `.agent/` anywhere → use git root (initial onboarding scenario)."""
    repo = tmp_path / "fresh"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)

    monkeypatch.chdir(sub)
    assert _project_dir() == repo


def test_project_dir_fallback_to_cwd(monkeypatch, tmp_path: Path) -> None:
    """No `.agent/` and no `.git/` → CWD (preserves backward compat)."""
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    assert _project_dir() == bare


def test_project_dir_ignores_agent_file(monkeypatch, tmp_path: Path) -> None:
    """`.agent` as a regular file (not a dir) must NOT be treated as the marker."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent").write_text("not a backlog")  # decoy
    sub = repo / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    # Falls through to .git → repo
    assert _project_dir() == repo


def test_project_dir_hops_from_git_file_to_main_root(monkeypatch, tmp_path: Path) -> None:
    """HATS-524: no `.agent/` ancestor + a `.git` *file* (linked worktree) →
    hop to the main checkout via `WorktreeManager.main_worktree_root`."""
    from ai_hats import wt as wt_mod

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    main = tmp_path / "main"
    main.mkdir()

    monkeypatch.setattr(
        wt_mod.WorktreeManager, "main_worktree_root", staticmethod(lambda _p: main)
    )
    monkeypatch.chdir(wt)
    assert _project_dir() == main


def test_project_dir_git_file_hop_failure_falls_back_to_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    """HATS-524: when the commondir hop returns None (git error, submodule,
    malformed pointer), fall back to the dir holding the `.git` file — never
    raise, never climb past the gitlink boundary."""
    from ai_hats import wt as wt_mod

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere\n")

    monkeypatch.setattr(
        wt_mod.WorktreeManager, "main_worktree_root", staticmethod(lambda _p: None)
    )
    monkeypatch.chdir(wt)
    assert _project_dir() == wt


def test_project_dir_main_repo_never_spawns_git(monkeypatch, repo_with_agent: Path) -> None:
    """HATS-524 contract: an onboarded repo resolves via the `.agent/` pass
    and MUST NOT reach the worktree commondir hop (no git subprocess)."""
    from ai_hats import wt as wt_mod

    def _boom(_p):
        raise AssertionError("main_worktree_root must not be called for a main repo")

    monkeypatch.setattr(
        wt_mod.WorktreeManager, "main_worktree_root", staticmethod(_boom)
    )
    monkeypatch.chdir(repo_with_agent)
    assert _project_dir() == repo_with_agent


# --- dead-cwd fail-loud (HATS-788) -------------------------------------


def test_project_dir_dead_cwd_raises_when_getcwd_errors(monkeypatch) -> None:
    """macOS variant: `Path.cwd()` raises FileNotFoundError on a removed cwd
    (e.g. a worktree torn down by `task done`). `_project_dir` must fail loud
    with `DeadCwdError`, not let the raw traceback escape."""
    from ai_hats.cli._helpers import DeadCwdError, _project_dir

    def _boom():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(Path, "cwd", staticmethod(_boom))
    with pytest.raises(DeadCwdError):
        _project_dir()


def test_project_dir_dead_cwd_raises_when_path_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Linux variant: `os.getcwd()` can return a stale path string for a
    removed directory instead of raising. A returned-but-nonexistent cwd must
    fail the same way — never fall through to Pass-4 + a phantom `.agent/`."""
    from ai_hats.cli._helpers import DeadCwdError, _project_dir

    missing = tmp_path / "gone"  # never created
    monkeypatch.setattr(Path, "cwd", staticmethod(lambda: missing))
    with pytest.raises(DeadCwdError):
        _project_dir()
    assert not (missing / ".agent").exists()  # no phantom resurrection


# --- exec_claude_with_retro (HATS-199) ---------------------------------


def test_exec_claude_with_retro_calls_execvp(monkeypatch, tmp_path: Path) -> None:
    """The helper must execvp into `claude` with one argv: a prompt that
    references the retro file. Captured by stubbing both shutil.which and
    os.execvp so the test process isn't actually replaced."""
    from ai_hats.cli import _helpers

    retro = tmp_path / ".agent/retrospectives/sessions/SID.md"
    retro.parent.mkdir(parents=True)
    retro.write_text("# retro")

    captured: dict[str, object] = {}

    def fake_execvp(path: str, args: list[str]) -> None:
        captured["path"] = path
        captured["args"] = args

    monkeypatch.setattr(_helpers.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(_helpers.os, "execvp", fake_execvp)
    monkeypatch.chdir(tmp_path)

    _helpers.exec_claude_with_retro(retro, kind="session")

    assert captured["path"] == "/usr/local/bin/claude"
    args = captured["args"]
    assert args[0] == "/usr/local/bin/claude"
    assert len(args) == 2
    prompt = args[1]
    assert "session retro" in prompt
    # Prompt should embed the relative path (not absolute) when possible
    assert ".agent/retrospectives/sessions/SID.md" in prompt


def test_exec_claude_with_retro_missing_binary(monkeypatch, tmp_path: Path) -> None:
    """If `claude` is not in PATH, the helper must exit with a clear error."""
    import pytest

    from ai_hats.cli import _helpers

    monkeypatch.setattr(_helpers.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit) as excinfo:
        _helpers.exec_claude_with_retro(tmp_path / "x.md", kind="session")
    assert excinfo.value.code == 1
