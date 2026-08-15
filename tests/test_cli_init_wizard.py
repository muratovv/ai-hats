"""Tests for `ai-hats self init` interactive wizard (HATS-347)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli.assembly import _detected_providers, _wizard_provider_prompt
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture()
def fresh_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


# ---------- _detected_providers ----------


def test_detect_lists_claude_when_dotclaude_exists(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == ["claude"]


@pytest.mark.parametrize(("home_dir", "provider"), [(".agy", "agy"), (".codex", "codex")])
def test_detect_lists_one_non_claude_provider(tmp_path, monkeypatch, home_dir, provider):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / home_dir).mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == [provider]


def test_detect_empty_when_neither(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == []


def test_detect_lists_both_when_both_present(tmp_path, monkeypatch):
    """Both home dirs present → BOTH detected, in PROVIDERS order (HATS-613).

    Pre-HATS-613 the helper returned a single string (the dict-first match,
    agy), hiding that claude was also installed.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".agy").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == ["claude", "agy"]


# ---------- _wizard_provider_prompt: marker + default policy ----------


def test_wizard_prompt_no_default_when_multiple_detected(monkeypatch):
    """2+ detected → ambiguous → no pre-filled click default (HATS-613)."""
    captured = {}

    def fake_prompt(text, default=None, show_default=False):
        captured["default"] = default
        captured["show_default"] = show_default
        return "claude"

    monkeypatch.setattr("ai_hats.cli.assembly.click.prompt", fake_prompt)
    assert _wizard_provider_prompt(["agy", "claude"]) == "claude"
    assert captured["default"] is None
    assert captured["show_default"] is False


def test_wizard_prompt_preselects_when_single_detected(monkeypatch):
    """Exactly one detected → that provider is the pre-filled default."""
    captured = {}

    def fake_prompt(text, default=None, show_default=False):
        captured["default"] = default
        captured["show_default"] = show_default
        return default  # simulate the user pressing Enter

    monkeypatch.setattr("ai_hats.cli.assembly.click.prompt", fake_prompt)
    # claude is index 1 in PROVIDERS order (claude, agy).
    assert _wizard_provider_prompt(["claude"]) == "claude"
    assert captured["default"] == "1"
    assert captured["show_default"] is True


def test_wizard_prompt_preselects_codex_and_reports_install(capsys):
    captured = {}

    def fake_prompt(text, default=None, show_default=False):
        captured["default"] = default
        return default

    assert (
        _wizard_provider_prompt(
            ["codex"],
            prompt=fake_prompt,
            installed_lookup=lambda name: name != "codex",
            provider_lookup=lambda name: pytest.fail("uninstalled provider was imported"),
        )
        == "codex"
    )
    assert captured["default"] == "4"
    assert "will install: ai-hats-codex" in capsys.readouterr().out


def test_init_wizard_marks_every_detected_provider(fresh_project, monkeypatch):
    """Menu marks BOTH detected providers `detected`; never `recommended`."""
    fake_home = fresh_project.parent / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".agy").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session"):
        result = runner.invoke(main, ["self", "init", "--no-update"], input="1\nclaude\n")
    assert result.exit_code == 0, result.output
    assert "Choose harness channel" in result.output
    assert "detected — found ~/.agy" in result.output
    assert "detected — found ~/.claude" in result.output
    assert "recommended" not in result.output


# ---------- init() flag-only paths (no wizard) ----------


@pytest.mark.parametrize(("provider", "role"), [("claude", "assistant"), ("codex", "maintainer")])
def test_init_with_both_flags_skips_wizard(fresh_project, provider, role):
    """When -p and -r are given, wizard must NOT auto-launch."""
    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        # stdin TTY behavior is irrelevant when both flags are present.
        result = runner.invoke(
            main,
            ["self", "init", "-p", provider, "-r", role, "--no-update"],
        )
    assert result.exit_code == 0, result.output
    assert (fresh_project / PROJECT_CONFIG).exists()
    assert f"provider: {provider}" in (fresh_project / PROJECT_CONFIG).read_text()
    launch.assert_not_called()


def test_init_no_wizard_flag_skips_wizard(fresh_project):
    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(
            main,
            ["self", "init", "-p", "claude", "--no-wizard"],
        )
    assert result.exit_code == 0, result.output
    launch.assert_not_called()


def test_init_no_tty_no_flags_fails_with_hint(fresh_project):
    """No TTY + no flags = fail-fast with a helpful message."""
    runner = CliRunner()  # CliRunner stdin is NOT a tty by default
    result = runner.invoke(main, ["self", "init"])
    assert result.exit_code == 2, result.output
    assert "TTY" in result.output or "--no-wizard" in result.output


# ---------- init() wizard path ----------


def test_init_wizard_invokes_launch_after_provider_prompt(fresh_project, monkeypatch):
    """TTY + no flags → prompts for harness & provider → minimal config → launches wizard."""
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(
            main,
            ["self", "init", "--no-update"],
            input="1\nclaude\n",
        )
    assert result.exit_code == 0, result.output
    assert "Choose harness channel" in result.output
    assert (fresh_project / PROJECT_CONFIG).exists()
    launch.assert_called_once()


def test_init_wizard_with_provider_flag_skips_cli_prompts(fresh_project, monkeypatch):
    """TTY + only -p (no -r) → skips CLI prompts, but wizard still launches."""
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(
            main,
            ["self", "init", "-p", "agy", "--no-update"],
        )
    assert result.exit_code == 0, result.output
    assert "Choose harness channel" not in result.output
    assert "Choose provider" not in result.output
    launch.assert_called_once()


def test_init_wizard_launches_on_reinit(fresh_project, monkeypatch):
    """Re-init in interactive TTY mode launches wizard and prompts harness+provider."""
    runner = CliRunner()
    # First initialize non-interactively
    runner.invoke(main, ["self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"])

    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(main, ["self", "init", "--no-update"], input="1\nclaude\n")
    assert result.exit_code == 0, result.output
    assert "Choose harness channel" in result.output
    launch.assert_called_once()


def test_init_never_builds_an_update_command(fresh_project, monkeypatch):
    """`self init` is local-only: it must never construct an install command.

    Guards the contract, not a symbol — the inline `_run_self_update` helper was
    deleted, so a patch on it would pass vacuously. `_build_update_cmd` is the
    single place any install command is assembled.
    """
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session"),
        patch("ai_hats.cli.maintenance._build_update_cmd") as build_cmd,
    ):
        result = runner.invoke(main, ["self", "init"], input="1\nclaude\n")
    assert result.exit_code == 0, result.output
    build_cmd.assert_not_called()


def test_init_flag_only_path_does_not_self_update(fresh_project):
    """Flag-only (CI) path must NOT trigger an install."""
    runner = CliRunner()
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session") as launch,
        patch("ai_hats.cli.maintenance._build_update_cmd") as build_cmd,
    ):
        result = runner.invoke(
            main, ["self", "init", "-p", "claude", "-r", "assistant", "--no-update"]
        )
    assert result.exit_code == 0, result.output
    build_cmd.assert_not_called()
    launch.assert_not_called()


def test_init_flag_only_persists_paths(fresh_project):
    """Non-wizard flag form writes ai_hats_dir + venv + no-manage-gitignore."""
    import yaml

    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session"):
        result = runner.invoke(
            main,
            [
                "self",
                "init",
                "-p",
                "claude",
                "-r",
                "assistant",
                "--ai-hats-dir",
                "agents/",
                "--venv",
                "~/.venvs/x",
                "--no-manage-gitignore",
            ],
        )
    assert result.exit_code == 0, result.output
    data = yaml.safe_load((fresh_project / PROJECT_CONFIG).read_text())
    assert data["ai_hats_dir"] == "agents"
    assert data["venv_path"]  # any non-empty value
    assert data["manage_gitignore"] is False
