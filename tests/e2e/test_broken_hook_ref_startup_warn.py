"""e2e (HATS-1509)

flow:   a developer launching a session when settings.json references a missing hook
cmds:
    ai-hats execute -r hook-role
expect: session start outputs a warning naming missing hook file and self init repair
        steps
why:    without startup warnings, stale hook references fail silently on tool calls with
        confusing harness errors
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _helpers.git import git
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

BROKEN_CMD = "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + minimal synthetic library (one role, one trait)."""
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "--quiet")
    git(project, "config", "user.email", "t@e.com")
    git(project, "config", "user.name", "t")

    lib = tmp_path / "lib"
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text("name: trait-base\ninjection: B.\n")
    role = lib / "roles" / "hook-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: hook-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: hook-role\n"
        "default_role: hook-role\n"
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
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )
    return result.output


def _write_hooks(project: Path, entries: list[dict]) -> None:
    settings_path = project / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings["hooks"] = {HOOK_PRE_TOOL_USE: entries} if entries else {}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings))


def test_session_start_warns_on_broken_managed_hook_ref(tmp_path: Path, monkeypatch):
    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("hook-role", provider_name="claude")
    # Isolate from the developer's real user-global settings.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))

    _write_hooks(
        project,
        [
            {
                "matcher": "Bash",
                "_ai_hats_managed": "ai-hats:hats-437",
                "hooks": [{"type": "command", "command": BROKEN_CMD}],
            }
        ],
    )
    assert not (project / ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh").exists()

    output = _launch(project, monkeypatch)

    assert "pre_bash_shared_state_guard.sh" in output, output
    assert "ai-hats self init --no-wizard" in output, output
    assert str(project) in output, output
    assert "startup warning" in output, output
    # HATS-1522: the old text sent the reader to `self update`, which reinstalls
    # the harness from GitHub instead of repairing this project.
    assert "self update" not in output, output

    # Removing the residue — what the named command does — silences the surface.
    _write_hooks(project, [])
    second = _launch(project, monkeypatch)
    assert "pre_bash_shared_state_guard.sh" not in second, second
    assert "harness reports an error" not in second, second
