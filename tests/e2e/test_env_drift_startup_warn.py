"""e2e (HATS-1192)

flow:   a developer starts an interactive session when background tooling packages in
        their environment have fallen behind project declarations
cmds:
    ai-hats agent assistant --task "Say hi"
expect: a pre-launch warning naming stale environment packages appears on stdout when
        drift is detected, and is suppressed when packages match
why:    unnoticed environment drift leads to subtle runtime failures when active CLI
        tools conflict with project specification
"""

from __future__ import annotations
from _helpers.git import git as _git_helper

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

DRIFT_TEXT = "dev env outdated: stale ai-hats-tracker 0.5.0 -> 0.6.0 — run 'uv sync'"


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + minimal synthetic library (one role, one trait)."""
    project = tmp_path / "project"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "t@e.com", cwd=project)
    _git("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text("name: trait-base\ninjection: B.\n")
    role = lib / "roles" / "drift-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: drift-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: drift-role\n"
        "default_role: drift-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _launch(project: Path, monkeypatch) -> str:
    from ai_hats import runtime as rt

    monkeypatch.setattr(
        rt.WrapRunner,
        "_pty_spawn",
        lambda self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None: 0,
    )
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", "0.05")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(project.parent / "claude-cfg"))
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )
    return result.output


def test_env_drift_warning_reaches_prelaunch_output(tmp_path: Path, monkeypatch):
    import ai_hats.env_drift

    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("drift-role", provider_name="claude")

    monkeypatch.setattr(ai_hats.env_drift, "stale_dev_env_warnings", lambda: [DRIFT_TEXT])
    output = _launch(project, monkeypatch)

    assert DRIFT_TEXT in output, output
    assert "startup warning" in output, output


def test_in_sync_env_launches_silent(tmp_path: Path, monkeypatch):
    import ai_hats.env_drift

    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("drift-role", provider_name="claude")

    monkeypatch.setattr(ai_hats.env_drift, "stale_dev_env_warnings", lambda: [])
    output = _launch(project, monkeypatch)

    assert "dev env outdated" not in output, output


def _git(*args: str, cwd: Path) -> None:
    _git_helper(cwd, *args)
