"""HATS-1006 — e2e: session start warns on deprecated Claude permission rules.

Model: ``test_skills_mirror_self_heals.py`` (real composition + materializers;
only the PTY spawn is stubbed). Guarantees, each fail-under-revert:

1. **Warn**: a deprecated ``Write(path)`` rule seeded into the project's
   ``.claude/settings.json`` is announced pre-spawn with its replacement.
2. **Clean**: after replacing it with the ``Edit(path)`` twin the second
   launch is silent for this surface (no false positive on Edit rules).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


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
    role = lib / "roles" / "lint-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: lint-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: lint-role\n"
        "default_role: lint-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _launch(project: Path, monkeypatch) -> str:
    from ai_hats import runtime as rt

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", lambda self, cmd, env, tracer: 0)
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", "0.05")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )
    return result.output


def _set_deny_rules(project: Path, rules: list[str]) -> None:
    """Merge deny rules into the materialized project settings, keeping wiring."""
    settings_path = project / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("permissions", {})["deny"] = rules
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings))


def test_session_start_warns_on_deprecated_write_rule(tmp_path: Path, monkeypatch):
    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("lint-role", provider_name="claude")
    # Isolate from the developer's real user-global settings.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))

    _set_deny_rules(project, ["Write(//**/.env)"])
    output = _launch(project, monkeypatch)

    assert "Write(//**/.env)" in output, output
    assert "Edit(//**/.env)" in output, output
    assert "startup warning" in output, output

    # The Edit twin is the documented fix — a fixed chain launches silent.
    _set_deny_rules(project, ["Edit(//**/.env)"])
    second = _launch(project, monkeypatch)
    assert "Edit(//**/.env)" not in second, second
    assert "is ignored by Claude Code" not in second, second
