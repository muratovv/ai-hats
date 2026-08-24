"""Tests for the ``init`` pipeline and its steps (HATS-1184)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.pipeline.harness import PipelineHarness
from ai_hats.pipeline.keys import KEY_EXECUTE_CMD, KEY_PROJECT_DIR, KEY_PROVIDER
from ai_hats.pipeline_catalog import INIT
from ai_hats.pipeline.steps.init_steps import InitProviderRequiredError, SelectProviderStep


def test_select_provider_step_guarantees_non_null_provider(tmp_path):
    step = SelectProviderStep()
    res = step.run(project_dir=tmp_path, provider="gemini")
    assert res[KEY_PROVIDER] == "gemini"


def test_select_provider_step_raises_on_non_tty_greenfield_no_flags(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: False)
    step = SelectProviderStep()
    with pytest.raises(InitProviderRequiredError):
        step.run(project_dir=tmp_path, provider=None, no_wizard=False)


def test_select_provider_step_fallback_claude_when_no_wizard(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: False)
    step = SelectProviderStep()
    res = step.run(project_dir=tmp_path, provider=None, no_wizard=True)
    assert res[KEY_PROVIDER] == "claude"


def test_init_pipeline_full_harness_run(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with (
        patch("ai_hats.cli.assembly._wizard_harness_prompt", return_value="stable"),
        patch("ai_hats.cli.assembly._wizard_provider_prompt", return_value="gemini"),
        patch("shutil.which", return_value="/usr/local/bin/ai-hats"),
    ):
        with PipelineHarness(INIT.name, tmp_path) as h:
            res = h.run({KEY_PROJECT_DIR: tmp_path})

        assert res[KEY_PROVIDER] == "gemini"
        assert res[KEY_EXECUTE_CMD] == [
            "/usr/local/bin/ai-hats",
            "execute",
            "--role",
            "initial-wizard",
            "--prompt",
            "initial-wizard",
            "--provider",
            "gemini",
        ]


def test_self_init_cli_launches_wizard_with_provider_flag(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ai_hats.cli.assembly._stdin_is_tty", lambda: True)
    with patch("ai_hats.cli.assembly._launch_wizard_session") as launch:
        result = runner.invoke(main, ["self", "init", "-p", "gemini", "--no-update"])
        assert result.exit_code == 0, result.output
        cmd = launch.call_args[0][0]
        assert cmd[1:] == [
            "execute",
            "--role",
            "initial-wizard",
            "--prompt",
            "initial-wizard",
            "--provider",
            "gemini",
        ]
