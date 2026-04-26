"""Tests for `ai-hats config feedback` CLI commands."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ai_hats.cli import main


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    # Minimal ai-hats.yaml (v2)
    (project / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )
    return project, CliRunner()


def _load_config(project):
    import yaml
    return yaml.safe_load((project / "ai-hats.yaml").read_text())


def test_config_feedback_show(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "show"])
    assert result.exit_code == 0, result.output
    assert "smart" in result.output
    assert "programmatic" in result.output


def test_config_feedback_session_retro_set_policy(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro", "off"])
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["policy"] == "off"


def test_config_feedback_session_retro_set_threshold(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "smart", "--threshold", "turns=15,tool_calls=20"]
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    sr = data["feedback"]["session_retro"]
    assert sr["smart_threshold"]["min_turns"] == 15
    assert sr["smart_threshold"]["min_tool_calls"] == 20


def test_config_feedback_session_retro_set_mode(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--mode", "llm"]
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["mode"] == "llm"


def test_config_feedback_session_retro_set_background(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--no-background"]
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["background"] is False


def test_config_feedback_session_retro_no_args_errors(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro"])
    assert result.exit_code != 0


def test_config_feedback_judge_set_policy(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "judge", "off"])
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["judge"]["policy"] == "off"


def test_config_feedback_roundtrip(cli_project):
    """Set values via CLI, verify show displays them."""
    project, runner = cli_project
    runner.invoke(main, ["config", "feedback", "session-retro", "hint", "--threshold", "turns=10"])

    result = runner.invoke(main, ["config", "feedback", "show"])
    assert "hint" in result.output
    assert "turns=10" in result.output


def test_config_feedback_show_includes_reminder_block(cli_project):
    """show prints reminder.* lines with default values."""
    _, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "show"])
    assert result.exit_code == 0, result.output
    assert "reminder.enabled" in result.output
    assert "reminder.max_skipped" in result.output
    assert "reminder.window_days" in result.output


def test_config_feedback_session_retro_set_reminder_max_skipped(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--reminder-max-skipped", "10"],
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["reminder"]["max_skipped"] == 10


def test_config_feedback_session_retro_set_reminder_window_days(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--reminder-window-days", "30"],
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["reminder"]["window_days"] == 30


def test_config_feedback_session_retro_no_reminder_disables(cli_project):
    """--no-reminder flips enabled=false without touching other reminder fields."""
    project, runner = cli_project
    # First set a custom max_skipped so we can verify it survives the disable.
    runner.invoke(
        main, ["config", "feedback", "session-retro", "--reminder-max-skipped", "7"],
    )

    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--no-reminder"],
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    rem = data["feedback"]["session_retro"]["reminder"]
    assert rem["enabled"] is False
    assert rem["max_skipped"] == 7  # untouched


def test_config_feedback_session_retro_reminder_combined_with_other_flags(cli_project):
    """Single invocation can set --mode and --reminder-* together."""
    project, runner = cli_project
    result = runner.invoke(main, [
        "config", "feedback", "session-retro",
        "--mode", "llm", "--reminder-max-skipped", "10", "--reminder-window-days", "30",
    ])
    assert result.exit_code == 0, result.output

    sr = _load_config(project)["feedback"]["session_retro"]
    assert sr["mode"] == "llm"
    assert sr["reminder"]["max_skipped"] == 10
    assert sr["reminder"]["window_days"] == 30


def test_config_feedback_session_retro_reminder_show_roundtrip(cli_project):
    """After setting reminder values, show prints them."""
    _, runner = cli_project
    runner.invoke(main, [
        "config", "feedback", "session-retro",
        "--reminder-max-skipped", "12", "--reminder-window-days", "21",
    ])

    result = runner.invoke(main, ["config", "feedback", "show"])
    assert "12" in result.output
    assert "21" in result.output
