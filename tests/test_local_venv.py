"""Tests for HATS-318 — opt-in local venv (`ai-hats self use-local|use-global`)
and the wrapper re-exec in :func:`ai_hats.cli.main_entry`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats import paths
from ai_hats.cli import main, _maybe_reexec_into_local_venv


@pytest.fixture()
def project(tmp_path, monkeypatch):
    p = tmp_path / "proj"
    p.mkdir()
    # Minimal yaml so paths.ai_hats_dir resolves predictably.
    (p / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "active_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )
    monkeypatch.chdir(p)
    return p


# ---------- paths.local_venv_path ----------


def test_local_venv_path_resolver(project):
    assert paths.local_venv_path(project) == project / ".agent" / "ai-hats" / ".venv"


def test_local_venv_path_respects_env_override(project, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(project / "custom"))
    assert paths.local_venv_path(project) == project / "custom" / ".venv"


# ---------- self use-local ----------


def test_use_local_creates_venv_and_installs(project, monkeypatch):
    """Happy path: subprocess.run is invoked once for `venv` and once for `pip install`."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # Simulate venv creation so the second call (pip) is reached.
        if "-m" in cmd and "venv" in cmd:
            target = Path(cmd[cmd.index("venv") + 1])
            (target / "bin").mkdir(parents=True, exist_ok=True)
            (target / "bin" / "pip").touch()
            (target / "bin" / "python").touch()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(main, ["self", "use-local"])
    assert result.exit_code == 0, result.output
    # First call → python -m venv ...
    assert calls[0][1:3] == ["-m", "venv"]
    # Second call → pip install ai-hats @ git+ssh://...
    assert calls[1][-1].startswith("ai-hats @ git+")


def test_use_local_idempotent_when_venv_exists(project, monkeypatch):
    """Pre-existing venv → warning, no subprocess invocations."""
    venv = paths.local_venv_path(project)
    venv.mkdir(parents=True)

    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(main, ["self", "use-local"])
    assert result.exit_code == 0, result.output
    assert "already exists" in result.output
    assert called is False


# ---------- self use-global ----------


def test_use_global_removes_venv(project):
    venv = paths.local_venv_path(project)
    venv.mkdir(parents=True)
    (venv / "marker").write_text("present")

    result = CliRunner().invoke(main, ["self", "use-global", "--yes"])
    assert result.exit_code == 0, result.output
    assert not venv.exists()


def test_use_global_noop_when_absent(project):
    result = CliRunner().invoke(main, ["self", "use-global", "--yes"])
    assert result.exit_code == 0, result.output
    assert "No local venv" in result.output


# ---------- wrapper re-exec ----------


def test_reexec_skipped_when_no_venv(project, monkeypatch):
    """No venv on disk → no execv call."""
    invoked = False

    def fake_execv(*args, **kwargs):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(os, "execv", fake_execv)
    _maybe_reexec_into_local_venv()
    assert invoked is False


def test_reexec_skipped_when_already_in_venv(project, monkeypatch):
    """sys.prefix already equals local venv root → no re-exec.

    HATS-329: comparison is on sys.prefix (venv root), not sys.executable
    (which symlinks to the host interpreter and would conflate two venvs
    sharing one Homebrew python3.14).
    """
    venv = paths.local_venv_path(project)
    venv_py = venv / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    invoked = False

    def fake_execv(*args, **kwargs):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(os, "execv", fake_execv)
    _maybe_reexec_into_local_venv()
    assert invoked is False


def test_reexec_invokes_execv_when_local_venv_present(project, monkeypatch):
    """Local venv exists + sys.prefix != local → execv is called once."""
    venv = paths.local_venv_path(project)
    venv_py = venv / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()

    captured: dict[str, object] = {}

    def fake_execv(path, argv):
        captured["path"] = path
        captured["argv"] = list(argv)

    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "argv", ["ai-hats", "task", "list"])
    monkeypatch.setattr(os, "execv", fake_execv)
    _maybe_reexec_into_local_venv()

    assert captured["path"] == str(venv_py)
    # argv = [python, -m, ai_hats, *forwarded args]
    assert captured["argv"][:3] == [str(venv_py), "-m", "ai_hats"]
    assert captured["argv"][3:] == ["task", "list"]
