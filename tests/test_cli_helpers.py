"""Tests for CLI helpers — project-root resolution (HATS-197).

The walk-up logic must find the nearest ancestor with `.agent/` (or
`.git/`) so commands run from a subdirectory don't materialize a stray
`.agent/` next to CWD and split the backlog DB across two places.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_with_agent(tmp_path: Path) -> Path:
    """Project root with `.agent/` already initialized."""
    root = tmp_path / "repo"
    (root / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    return root


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
