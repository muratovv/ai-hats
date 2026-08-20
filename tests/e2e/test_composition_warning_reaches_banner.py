"""e2e (HATS-1753)

flow:   a developer launching a session whose role declares an app nothing collects
cmds:
    ai-hats execute -r warn-role
expect: session start prints the composition's warning, naming the YAML that
        declared it, before the wrapped CLI tears the terminal into alt-screen
why:    composition warnings used to go straight to stderr, which the alternate
        screen buffer eats — a gate that never fires, announced to nobody
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers.git import git
from click.testing import CliRunner

from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + a synthetic library whose role declares a foreign app."""
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "--quiet")
    git(project, "config", "user.email", "t@e.com")
    git(project, "config", "user.name", "t")

    lib = tmp_path / "lib"
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text("name: trait-base\ninjection: B.\n")

    skill = lib / "skills" / "gate-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: gate-skill\ndescription: A skill shipping one gate script.\n---\n"
    )
    script = skill / "g.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)

    role = lib / "roles" / "warn-role"
    role.mkdir(parents=True)
    role_config = role / "config.yaml"
    role_config.write_text(
        "name: warn-role\npriorities: [Quality]\n"
        "composition:\n"
        "  traits:\n    - trait-base\n"
        "  skills:\n    - gate-skill\n"
        "  apps:\n"
        "    nosuchapp:\n"
        "      - run: gate-skill/g.sh\n"
        "        at: [some-point]\n"
        "injection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: warn-role\n"
        "default_role: warn-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, role_config


def _launch(project: Path, monkeypatch, hold: str = "0.05") -> str:
    from ai_hats import runtime as rt
    from ai_hats.cli import main

    monkeypatch.setattr(
        rt.WrapRunner,
        "_pty_spawn",
        lambda self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None: 0,
    )
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", hold)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )
    return result.output


def test_composition_warning_reaches_the_pre_launch_banner(tmp_path, monkeypatch):
    """The whole chain: composer -> sink -> payload -> notice -> pre-spawn render."""
    project, role_config = _make_project(tmp_path)

    output = _launch(project, monkeypatch)

    assert "nosuchapp" in output, "the composition's finding must reach the banner"
    assert "will never fire" in output
    assert str(role_config) in output, "and it must name the YAML to open"


def test_a_headless_launch_shows_the_warning_it_does_not_wait_for(tmp_path, monkeypatch):
    """HATS-1753: with the hold at zero the launch must still SAY what it found.
    This is the prong that fails on the pre-fix code — the notice was written to
    diagnostics.json and never rendered, so CI and subagent runs saw nothing."""
    project, role_config = _make_project(tmp_path)

    output = _launch(project, monkeypatch, hold="0")

    assert "nosuchapp" in output
    assert str(role_config) in output
