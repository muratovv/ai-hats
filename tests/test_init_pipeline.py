"""The ``init`` pipeline and its steps: collaborators arrive in the state, notices go out."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli.assembly import AssemblerBootstrapper
from ai_hats.config import Channel
from ai_hats.config.project import ProjectConfig
from ai_hats.initialization import InitProviderRequiredError
from ai_hats.pipeline.harness import PipelineHarness
from ai_hats.pipeline.keys import (
    KEY_BOOTSTRAPPER,
    KEY_EXECUTE_CMD,
    KEY_NOTICES,
    KEY_PROJECT_DIR,
    KEY_PROVIDER,
    KEY_WIZARD,
)
from ai_hats.pipeline.steps.init_steps import SelectProviderStep
from ai_hats.pipeline_catalog import INIT
from ai_hats.surface_registry import get_surface
from ai_hats_core.layout import ProjectLayout


class FakeWizard:
    """Answers scripted in advance; records what it was asked."""

    def __init__(self, *, tty: bool, provider: str = "agy", channel: str = "stable") -> None:
        self._tty = tty
        self._provider = provider
        self._channel = channel
        self.asked: list[str] = []

    def is_interactive(self) -> bool:
        return self._tty

    def detected_providers(self):
        return [get_surface(self._provider)]

    def choose_provider(self, detected):
        self.asked.append("provider")
        return get_surface(self._provider)

    def choose_channel(self, current):
        self.asked.append("channel")
        return Channel(self._channel)


class BareBootstrapper:
    """A bootstrapper for a project that does not exist yet: defaults, no writes."""

    config = ProjectConfig()

    def init(self, **kw):  # pragma: no cover - the select step never writes
        raise AssertionError("select_provider must not init")

    def verify_runtime_hooks(self, *, backup_path):  # pragma: no cover
        raise AssertionError

    def report_diagnostics(self):  # pragma: no cover
        raise AssertionError


def test_select_provider_step_guarantees_non_null_provider(tmp_path):
    res = SelectProviderStep().run(
        project_dir=tmp_path,
        wizard=FakeWizard(tty=False),
        bootstrapper=BareBootstrapper(),
        provider="gemini",
    )
    assert res[KEY_PROVIDER] == "gemini"


def test_select_provider_step_raises_on_non_tty_greenfield_no_flags(tmp_path):
    with pytest.raises(InitProviderRequiredError):
        SelectProviderStep().run(
            project_dir=tmp_path,
            wizard=FakeWizard(tty=False),
            bootstrapper=BareBootstrapper(),
            provider=None,
            no_wizard=False,
        )


def test_select_provider_step_fallback_claude_when_no_wizard(tmp_path):
    res = SelectProviderStep().run(
        project_dir=tmp_path,
        wizard=FakeWizard(tty=False),
        bootstrapper=BareBootstrapper(),
        provider=None,
        no_wizard=True,
    )
    assert res[KEY_PROVIDER] == "claude"


def test_select_provider_step_asks_the_wizard_when_interactive(tmp_path):
    wizard = FakeWizard(tty=True, provider="agy", channel="edge")
    res = SelectProviderStep().run(
        project_dir=tmp_path, wizard=wizard, bootstrapper=BareBootstrapper()
    )
    assert res[KEY_PROVIDER] == "agy"
    assert res["channel"] is Channel.EDGE
    assert wizard.asked == ["channel", "provider"]


def test_init_pipeline_full_harness_run(tmp_path):
    with patch("shutil.which", return_value="/usr/local/bin/ai-hats"):
        with PipelineHarness(INIT.name, ProjectLayout.at(tmp_path)) as h:
            res = h.run(
                {
                    KEY_PROJECT_DIR: tmp_path,
                    KEY_WIZARD: FakeWizard(tty=True, provider="agy"),
                    KEY_BOOTSTRAPPER: AssemblerBootstrapper(tmp_path),
                }
            )

    assert res[KEY_PROVIDER] == "agy"
    assert res[KEY_EXECUTE_CMD] == [
        "/usr/local/bin/ai-hats",
        "execute",
        "--role",
        "initial-wizard",
        "--prompt",
        "initial-wizard",
        "--provider",
        "agy",
    ]
    assert any("Initialized" in line for line in res[KEY_NOTICES])
    assert (tmp_path / "ai-hats.yaml").exists()


def test_steps_print_nothing_themselves(tmp_path, capsys):
    """What a step has to say comes back as notices; the runner prints."""
    with patch("shutil.which", return_value="/usr/local/bin/ai-hats"):
        with PipelineHarness(INIT.name, ProjectLayout.at(tmp_path)) as h:
            res = h.run(
                {
                    KEY_PROJECT_DIR: tmp_path,
                    KEY_WIZARD: FakeWizard(tty=False),
                    KEY_BOOTSTRAPPER: AssemblerBootstrapper(tmp_path),
                    KEY_PROVIDER: "gemini",
                }
            )
    assert res[KEY_NOTICES]
    assert "Initialized" not in capsys.readouterr().out


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
        assert "Initialized" in result.output


def test_self_init_refuses_an_unreadable_config_before_the_pipeline(tmp_path, monkeypatch):
    """Re-init on a yaml from the future refuses at the root, exit 2, no reset."""
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 999\nprovider: agy\n")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["self", "init", "-p", "gemini", "--no-update"])
    assert result.exit_code == 2, result.output
    assert "could not be read" in result.output
    assert "provider: agy" in (tmp_path / "ai-hats.yaml").read_text()


def test_console_is_the_runners_not_the_steps():
    """A step's io names notices, never a console."""
    from ai_hats.pipeline.steps.init_steps import (
        BootstrapProjectStep,
        PrepareExecuteSessionStep,
    )

    for step in (SelectProviderStep(), BootstrapProjectStep(), PrepareExecuteSessionStep()):
        assert "console" not in step.io.requires | step.io.optional
