"""E2E tests for full system initialization flow via CLI."""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.library import LibraryResolver
from ai_hats.models import ComponentType


def _all_roles() -> list[str]:
    """Discover all roles from the built-in library."""
    from pathlib import Path

    builtin = Path(__file__).resolve().parent.parent / "src" / "ai_hats" / "libraries"
    resolver = LibraryResolver([builtin])
    return sorted(resolver.list_components(ComponentType.ROLE))


ALL_ROLES = _all_roles()


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    """Clean project directory with chdir and CliRunner."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project, CliRunner()


def test_init_creates_project(cli_project):
    """ai-hats init --role <role> --provider claude creates all artifacts."""
    project, runner = cli_project

    result = runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])

    assert result.exit_code == 0, result.output
    assert (project / "ai-hats.yaml").exists()
    assert (project / "profile.json").exists()
    assert (project / ".agent" / "rules").is_dir()
    assert (project / ".agent" / "skills").is_dir()
    assert (project / ".agent" / "backlog" / "tasks").is_dir()
    assert (project / "CLAUDE.md").exists()
    assert len((project / "CLAUDE.md").read_text()) > 100


@pytest.mark.parametrize("role", ALL_ROLES, ids=ALL_ROLES)
def test_set_all_roles(cli_project, role):
    """Every built-in role assembles without errors via CLI."""
    project, runner = cli_project

    # init first
    r = runner.invoke(main, ["init", "--provider", "claude"])
    assert r.exit_code == 0, r.output

    # set role
    r = runner.invoke(main, ["set", role, "--provider", "claude"])
    assert r.exit_code == 0, r.output
    assert "Warning" not in r.output
    assert (project / "CLAUDE.md").exists()
    assert len((project / "CLAUDE.md").read_text()) > 100


def test_status_after_set(cli_project):
    """ai-hats status shows role and components after set."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--provider", "claude"])
    runner.invoke(main, ["set", ALL_ROLES[0], "--provider", "claude"])

    r = runner.invoke(main, ["status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_bump_after_set(cli_project):
    """ai-hats bump re-assembles without errors."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--provider", "claude"])
    runner.invoke(main, ["set", ALL_ROLES[0], "--provider", "claude"])

    prompt_before = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["bump"])
    assert r.exit_code == 0, r.output
    assert "Bumped" in r.output

    prompt_after = (project / "CLAUDE.md").read_text()
    assert len(prompt_after) > 100
    assert prompt_before == prompt_after


def test_init_idempotent_via_cli(cli_project):
    """Repeated init does not break existing state."""
    project, runner = cli_project

    runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])
    prompt_first = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["init", "--role", ALL_ROLES[0], "--provider", "claude"])
    assert r.exit_code == 0, r.output

    prompt_second = (project / "CLAUDE.md").read_text()
    assert prompt_first == prompt_second


# -- Role override (shadow prompt) e2e tests --


def test_override_creates_shadow_prompt_without_modifying_project(cli_project):
    """--role override produces a temp file and leaves CLAUDE.md untouched."""
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.models import ProfileConfig
    from ai_hats.providers import ClaudeProvider

    project, runner = cli_project

    # Init + set base role
    runner.invoke(main, ["init", "--role", "assistant", "--provider", "claude"])
    original_claude = (project / "CLAUDE.md").read_text()
    original_profile = ProfileConfig.load(project / "profile.json")
    assert original_profile.active_role == "assistant"

    # Build override for a different role (simulate what WrapRunner.run does)
    asm = Assembler(project)
    provider = ClaudeProvider()
    result = asm.composer.compose("judge")
    args, env = provider.build_override(project, result, None)

    # Shadow prompt created
    assert args[0] == "--system-prompt-file"
    override_path = Path(args[1])
    assert override_path.exists()
    override_content = override_path.read_text()
    assert "judge" in override_content.lower() or "SESSION" in override_content

    # Project files NOT modified
    assert (project / "CLAUDE.md").read_text() == original_claude
    after_profile = ProfileConfig.load(project / "profile.json")
    assert after_profile.active_role == "assistant"

    override_path.unlink()


def test_multiple_parallel_overrides_are_independent(cli_project):
    """Multiple simultaneous role overrides get independent temp files."""
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.providers import ClaudeProvider

    project, runner = cli_project
    runner.invoke(main, ["init", "--role", "assistant", "--provider", "claude"])

    asm = Assembler(project)
    provider = ClaudeProvider()

    # Simulate 3 parallel override sessions for different roles
    overrides = {}
    for role in ("judge", "go-dev", "architect"):
        result = asm.composer.compose(role)
        args, _ = provider.build_override(project, result, None)
        override_path = Path(args[1])
        overrides[role] = {
            "path": override_path,
            "content": override_path.read_text(),
        }

    # All temp files exist simultaneously
    for role, info in overrides.items():
        assert info["path"].exists(), f"Override file for {role} missing"

    # All temp files are distinct paths
    paths = [str(info["path"]) for info in overrides.values()]
    assert len(set(paths)) == 3, "Override files must be distinct"

    # Each contains its own role content, not another role's
    assert "judge" in overrides["judge"]["content"].lower() or "SESSION" in overrides["judge"]["content"]
    assert "GO DEVELOPER" in overrides["go-dev"]["content"]
    assert "architect" in overrides["architect"]["content"].lower() or "ARCHITECT" in overrides["architect"]["content"]

    # Project CLAUDE.md unchanged through all this
    claude_content = (project / "CLAUDE.md").read_text()
    assert "assistant" in claude_content.lower() or "PRIMARY" in claude_content

    # Cleanup
    for info in overrides.values():
        info["path"].unlink()


def test_gemini_override_creates_session_rules_dir(cli_project):
    """Gemini override uses GEMINI_CLI_PROJECT_RULES_PATH with isolated rules dir."""
    import shutil
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.providers import GeminiProvider

    project, runner = cli_project
    runner.invoke(main, ["init", "--role", "assistant", "--provider", "gemini"])

    asm = Assembler(project)
    provider = GeminiProvider()

    # Build two parallel overrides
    result_a = asm.composer.compose("judge")
    _, env_a = provider.build_override(project, result_a, None)
    result_b = asm.composer.compose("go-dev")
    _, env_b = provider.build_override(project, result_b, None)

    dir_a = Path(env_a["GEMINI_CLI_PROJECT_RULES_PATH"])
    dir_b = Path(env_b["GEMINI_CLI_PROJECT_RULES_PATH"])

    # Independent dirs
    assert dir_a != dir_b
    assert dir_a.exists()
    assert dir_b.exists()

    # Each has its own mandatory role file
    assert (dir_a / "00_MANDATORY_ROLE.md").exists()
    assert (dir_b / "00_MANDATORY_ROLE.md").exists()
    assert (dir_a / "00_MANDATORY_ROLE.md").read_text() != (dir_b / "00_MANDATORY_ROLE.md").read_text()

    # GEMINI.md untouched
    gemini_content = (project / "GEMINI.md").read_text()
    assert "assistant" in gemini_content.lower() or "PRIMARY" in gemini_content

    shutil.rmtree(dir_a)
    shutil.rmtree(dir_b)
