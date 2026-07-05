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


def test_detect_lists_gemini_when_only_dotgemini_exists(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".gemini").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == ["gemini"]


def test_detect_empty_when_neither(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == []


def test_detect_lists_both_when_both_present(tmp_path, monkeypatch):
    """Both home dirs present → BOTH detected, in PROVIDERS order (HATS-613).

    Pre-HATS-613 the helper returned a single string (the dict-first match,
    gemini), hiding that claude was also installed.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".gemini").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    assert _detected_providers() == ["gemini", "claude"]


# ---------- _wizard_provider_prompt: marker + default policy ----------


def test_wizard_prompt_no_default_when_multiple_detected(monkeypatch):
    """2+ detected → ambiguous → no pre-filled click default (HATS-613)."""
    captured = {}

    def fake_prompt(text, default=None, show_default=False):
        captured["default"] = default
        captured["show_default"] = show_default
        return "claude"

    monkeypatch.setattr("ai_hats.cli.assembly.click.prompt", fake_prompt)
    assert _wizard_provider_prompt(["gemini", "claude"]) == "claude"
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
    # claude is index 2 in PROVIDERS order (gemini, claude).
    assert _wizard_provider_prompt(["claude"]) == "claude"
    assert captured["default"] == "2"
    assert captured["show_default"] is True


def test_init_wizard_marks_every_detected_provider(fresh_project, monkeypatch):
    """Menu marks BOTH detected providers `detected`; never `recommended`."""
    fake_home = fresh_project.parent / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".gemini").mkdir()
    monkeypatch.setattr("ai_hats.cli.assembly.Path.home", lambda: fake_home)
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    runner = CliRunner()
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session"),
        patch("ai_hats.cli.assembly._run_self_update"),
    ):
        result = runner.invoke(main, ["self", "init", "--no-update"], input="claude\n")
    assert result.exit_code == 0, result.output
    assert "detected — found ~/.gemini" in result.output
    assert "detected — found ~/.claude" in result.output
    assert "recommended" not in result.output


# ---------- init() flag-only paths (no wizard) ----------


def test_init_with_both_flags_skips_wizard(fresh_project):
    """When -p and -r are given, wizard must NOT auto-launch."""
    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        # stdin TTY behavior is irrelevant when both flags are present.
        result = runner.invoke(
            main, ["self", "init", "-p", "claude", "-r", "assistant"],
        )
    assert result.exit_code == 0, result.output
    assert (fresh_project / PROJECT_CONFIG).exists()
    launch.assert_not_called()


def test_init_no_wizard_flag_skips_wizard(fresh_project):
    runner = CliRunner()
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(
            main, ["self", "init", "-p", "claude", "--no-wizard"],
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
    """TTY + no flags → prompts for provider → minimal config → launches wizard."""
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session") as launch,
        patch("ai_hats.cli.assembly._run_self_update") as upd,
    ):
        # --no-update keeps the test offline-safe; the update path is
        # exercised separately below.
        # HATS-366: the CLI advanced-setup gate is gone; provider is the
        # only prompt before launching the LLM wizard session.
        result = runner.invoke(
            main, ["self", "init", "--no-update"], input="claude\n",
        )
    assert result.exit_code == 0, result.output
    assert (fresh_project / PROJECT_CONFIG).exists()
    launch.assert_called_once()
    upd.assert_not_called()


def test_init_wizard_with_provider_flag_skips_provider_prompt(fresh_project, monkeypatch):
    """TTY + only -p (no -r) → no provider prompt, but wizard still launches."""
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session") as launch,
        patch("ai_hats.cli.assembly._run_self_update"),
    ):
        # Provider via flag — no remaining CLI prompts (HATS-366 removed
        # the advanced-setup gate). LLM wizard handles paths/venv/gitignore.
        result = runner.invoke(
            main, ["self", "init", "-p", "gemini", "--no-update"],
        )
    assert result.exit_code == 0, result.output
    assert "Choose provider" not in result.output
    launch.assert_called_once()


def test_init_wizard_runs_self_update_by_default(fresh_project, monkeypatch):
    """Wizard path (TTY, no flags, no --no-update) calls _run_self_update once."""
    runner = CliRunner()
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session"),
        patch("ai_hats.cli.assembly._run_self_update", return_value=True) as upd,
    ):
        result = runner.invoke(main, ["self", "init"], input="claude\n")
    assert result.exit_code == 0, result.output
    upd.assert_called_once()


def test_init_flag_only_path_does_not_self_update(fresh_project):
    """Flag-only (CI) path must NOT trigger pip install."""
    runner = CliRunner()
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session") as launch,
        patch("ai_hats.cli.assembly._run_self_update") as upd,
    ):
        result = runner.invoke(main, ["self", "init", "-p", "claude", "-r", "assistant"])
    assert result.exit_code == 0, result.output
    upd.assert_not_called()
    launch.assert_not_called()


def test_init_flag_only_persists_paths(fresh_project):
    """Non-wizard flag form writes ai_hats_dir + venv + no-manage-gitignore."""
    import yaml

    runner = CliRunner()
    with (
        patch("ai_hats.cli.assembly._launch_wizard_session"),
        patch("ai_hats.cli.assembly._run_self_update"),
    ):
        result = runner.invoke(
            main,
            [
                "self", "init",
                "-p", "claude", "-r", "assistant",
                "--ai-hats-dir", "agents/",
                "--venv", "~/.venvs/x",
                "--no-manage-gitignore",
            ],
        )
    assert result.exit_code == 0, result.output
    data = yaml.safe_load((fresh_project / PROJECT_CONFIG).read_text())
    assert data["ai_hats_dir"] == "agents"
    assert data["venv_path"]  # any non-empty value
    assert data["manage_gitignore"] is False
