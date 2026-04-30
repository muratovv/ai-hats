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


# --- exec_claude_with_retro (HATS-199) ---------------------------------


def test_exec_claude_with_retro_calls_execvp(monkeypatch, tmp_path: Path) -> None:
    """The helper must execvp into `claude` with one argv: a prompt that
    references the retro file. Captured by stubbing both shutil.which and
    os.execvp so the test process isn't actually replaced."""
    from ai_hats.cli import _helpers

    retro = tmp_path / ".agent/retrospectives/sessions/llm/SID.md"
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
    assert ".agent/retrospectives/sessions/llm/SID.md" in prompt


def test_exec_claude_with_retro_judge_label(monkeypatch, tmp_path: Path) -> None:
    """kind='judge' must change the prompt label so the chat opens with judge framing."""
    from ai_hats.cli import _helpers

    retro = tmp_path / "judge.md"
    retro.write_text("# judge")

    captured: dict[str, object] = {}

    monkeypatch.setattr(_helpers.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(
        _helpers.os, "execvp",
        lambda path, args: captured.update({"path": path, "args": args}),
    )

    _helpers.exec_claude_with_retro(retro, kind="judge")

    assert "judge retro" in captured["args"][1]


def test_exec_claude_with_retro_missing_binary(monkeypatch, tmp_path: Path) -> None:
    """If `claude` is not in PATH, the helper must exit with a clear error."""
    import pytest

    from ai_hats.cli import _helpers

    monkeypatch.setattr(_helpers.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit) as excinfo:
        _helpers.exec_claude_with_retro(tmp_path / "x.md", kind="session")
    assert excinfo.value.code == 1
