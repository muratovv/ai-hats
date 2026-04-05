"""E2E tests for full system initialization flow via CLI."""

from __future__ import annotations

import os
import sys

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


def test_set_creates_project(cli_project):
    """ai-hats set -r <role> -p claude auto-inits and creates all artifacts."""
    project, runner = cli_project

    result = runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])

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

    r = runner.invoke(main, ["set", "-r", role, "-p", "claude"])
    assert r.exit_code == 0, r.output
    assert "Warning" not in r.output
    assert (project / "CLAUDE.md").exists()
    assert len((project / "CLAUDE.md").read_text()) > 100


def test_status_after_set(cli_project):
    """ai-hats status shows role and components after set."""
    project, runner = cli_project

    runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])

    r = runner.invoke(main, ["status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_bump_after_set(cli_project):
    """ai-hats bump re-assembles without errors."""
    project, runner = cli_project

    runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])

    prompt_before = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["bump"])
    assert r.exit_code == 0, r.output
    assert "Bumped" in r.output

    prompt_after = (project / "CLAUDE.md").read_text()
    assert len(prompt_after) > 100
    assert prompt_before == prompt_after


def test_set_idempotent_via_cli(cli_project):
    """Repeated set does not break existing state."""
    project, runner = cli_project

    runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])
    prompt_first = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])
    assert r.exit_code == 0, r.output

    prompt_second = (project / "CLAUDE.md").read_text()
    assert prompt_first == prompt_second


# -- Role override (shadow prompt) e2e tests --


def test_passthrough_args_reach_main_context(cli_project):
    """Unknown flags like --resume are collected in ctx.args, not rejected."""
    project, runner = cli_project

    # ai-hats --resume should NOT fail with "no such option"
    # It will fail because WrapRunner can't actually launch a provider,
    # but the important thing is it doesn't fail at Click parsing level.
    result = runner.invoke(main, ["--resume"])
    # Should not be a Click UsageError about unknown option
    assert "No such option" not in (result.output or "")
    assert "no such option" not in (result.output or "")


def test_subcommands_work_with_passthrough_context(cli_project):
    """Subcommands like set/status still work despite ignore_unknown_options."""
    project, runner = cli_project

    r = runner.invoke(main, ["set", "-r", ALL_ROLES[0], "-p", "claude"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_override_creates_shadow_prompt_without_modifying_project(cli_project):
    """--role override produces a temp file and leaves CLAUDE.md untouched."""
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.models import ProfileConfig
    from ai_hats.providers import ClaudeProvider

    project, runner = cli_project

    # Init + set base role
    runner.invoke(main, ["set", "-r", "assistant", "-p", "claude"])
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
    runner.invoke(main, ["set", "-r", "assistant", "-p", "claude"])

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
    runner.invoke(main, ["set", "-r", "assistant", "-p", "gemini"])

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


# -- Update command tests --


def test_update_command_uses_force_reinstall():
    """Update command must use --force-reinstall to bypass pip cache."""
    from ai_hats.cli import _build_update_cmd

    cmd = _build_update_cmd()
    assert "--force-reinstall" in cmd, "pip caches git installs; --force-reinstall is required"
    assert "--no-cache-dir" in cmd, "pip caches wheels; --no-cache-dir forces fresh git clone"
    assert "--no-deps" in cmd, "--no-deps avoids re-downloading all dependencies"
    assert any("git+ssh://" in arg for arg in cmd), "must install from git"
    assert cmd[0] == sys.executable, "must use current Python interpreter"
    assert "pip" in cmd, "must use pip"


def test_update_command_runs_via_cli(cli_project, monkeypatch):
    """ai-hats update invokes pip with correct flags (mocked subprocess)."""
    import subprocess

    project, runner = cli_project

    # Init project so migrate doesn't fail
    runner.invoke(main, ["set", "-p", "claude"])

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        # For version check subprocess, return a version string
        stdout = "0.3.0" if "__version__" in str(cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["update"])
    assert result.exit_code == 0, result.output
    assert "Updating from GitHub" in result.output

    # Find the pip install command among all subprocess calls
    pip_cmds = [c for c in captured_cmds if "--force-reinstall" in c]
    assert len(pip_cmds) == 1, f"Expected 1 pip install call, got {len(pip_cmds)}"
    pip_cmd = pip_cmds[0]
    assert "--no-deps" in pip_cmd
    assert any("git+ssh://git@github.com/muratovv/ai-hats.git" in arg for arg in pip_cmd)


def test_update_command_reports_failure(cli_project, monkeypatch):
    """ai-hats update shows error when pip fails."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["set", "-p", "claude"])

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["update"])
    assert "Update failed" in result.output
    assert "Connection refused" in result.output


def test_update_shows_version_transition(cli_project, monkeypatch):
    """ai-hats update shows old → new version when version changes."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["set", "-p", "claude"])

    def mock_run(cmd, **kwargs):
        # Version check returns new version
        if "__version__" in str(cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout="0.5.0\n", stderr="")
        # git clone for changelog — simulate success
        if "git" in cmd and "clone" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # git log for changelog
        if "git" in cmd and "log" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout="abc1234 feat: new feature\ndef5678 fix: bug fix\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["update"])
    assert result.exit_code == 0, result.output
    # Shows version transition
    assert "0.5.0" in result.output
    # Shows changelog
    assert "Recent changes" in result.output
    assert "new feature" in result.output


def test_update_shows_already_up_to_date(cli_project, monkeypatch):
    """ai-hats update shows 'already up to date' when versions match."""
    import subprocess

    from ai_hats import __version__

    project, runner = cli_project
    runner.invoke(main, ["set", "-p", "claude"])

    def mock_run(cmd, **kwargs):
        if "__version__" in str(cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{__version__}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["update"])
    assert result.exit_code == 0, result.output
    assert "Already up to date" in result.output
