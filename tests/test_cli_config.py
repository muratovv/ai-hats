"""Tests for `ai-hats config feedback` CLI commands."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats.cli import main


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    # Minimal profile.json
    (project / "profile.json").write_text(
        json.dumps({"active_role": "assistant", "provider": "claude"})
    )
    return project, CliRunner()


def _load_profile(project):
    return json.loads((project / "profile.json").read_text())


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

    data = _load_profile(project)
    assert data["feedback"]["session_retro"]["policy"] == "off"


def test_config_feedback_session_retro_set_threshold(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "smart", "--threshold", "turns=15,tool_calls=20"]
    )
    assert result.exit_code == 0, result.output

    data = _load_profile(project)
    sr = data["feedback"]["session_retro"]
    assert sr["smart_threshold"]["min_turns"] == 15
    assert sr["smart_threshold"]["min_tool_calls"] == 20


def test_config_feedback_session_retro_set_mode(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--mode", "llm"]
    )
    assert result.exit_code == 0, result.output

    data = _load_profile(project)
    assert data["feedback"]["session_retro"]["mode"] == "llm"


def test_config_feedback_session_retro_set_background(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "feedback", "session-retro", "--no-background"]
    )
    assert result.exit_code == 0, result.output

    data = _load_profile(project)
    assert data["feedback"]["session_retro"]["background"] is False


def test_config_feedback_session_retro_no_args_errors(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro"])
    assert result.exit_code != 0


def test_config_feedback_judge_set_policy(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "judge", "off"])
    assert result.exit_code == 0, result.output

    data = _load_profile(project)
    assert data["feedback"]["judge"]["policy"] == "off"


def test_config_feedback_roundtrip(cli_project):
    """Set values via CLI, verify show displays them."""
    project, runner = cli_project
    runner.invoke(main, ["config", "feedback", "session-retro", "hint", "--threshold", "turns=10"])

    result = runner.invoke(main, ["config", "feedback", "show"])
    assert "hint" in result.output
    assert "turns=10" in result.output
