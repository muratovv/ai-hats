"""Tests for `ai-hats config feedback` CLI commands."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    # Minimal ai-hats.yaml (v2)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\nactive_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )
    return project, CliRunner()


def _load_config(project):
    import yaml

    return yaml.safe_load((project / PROJECT_CONFIG).read_text())


def test_config_feedback_show(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "show"])
    assert result.exit_code == 0, result.output
    assert "smart" in result.output


def test_config_feedback_session_retro_set_policy(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro", "off"])
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["policy"] == "off"


def test_config_feedback_session_retro_set_threshold(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main,
        ["config", "feedback", "session-retro", "smart", "--threshold", "turns=15,tool_calls=20"],
    )
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    sr = data["feedback"]["session_retro"]
    assert sr["smart_threshold"]["min_turns"] == 15
    assert sr["smart_threshold"]["min_tool_calls"] == 20


def test_config_feedback_session_retro_set_background(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro", "--no-background"])
    assert result.exit_code == 0, result.output

    data = _load_config(project)
    assert data["feedback"]["session_retro"]["background"] is False


def test_config_feedback_session_retro_no_args_errors(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "feedback", "session-retro"])
    assert result.exit_code != 0


def test_config_feedback_roundtrip(cli_project):
    """Set values via CLI, verify show displays them."""
    project, runner = cli_project
    runner.invoke(main, ["config", "feedback", "session-retro", "hint", "--threshold", "turns=10"])

    result = runner.invoke(main, ["config", "feedback", "show"])
    assert "hint" in result.output
    assert "turns=10" in result.output


def test_config_set_runtime_composition_refused(cli_project):
    """HATS-1456 R5: config set -r refuses expressions with operators."""
    project, runner = cli_project
    result = runner.invoke(main, ["config", "set", "-r", "assistant + leader"])
    assert result.exit_code != 0
    assert "runtime composition ('+' / '-') cannot be persisted" in result.output


def test_config_customize_runtime_composition_refused(cli_project):
    """HATS-1456 R5: config customize refuses role names with operators."""
    project, runner = cli_project
    result = runner.invoke(
        main, ["config", "customize", "assistant + leader", "--add-trait", "foo"]
    )
    assert result.exit_code != 0
    assert "runtime composition ('+' / '-') cannot be persisted" in result.output
