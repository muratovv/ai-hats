"""Tests for `ai-hats config set` advanced flags (HATS-366):
--venv / --no-venv, --manage-gitignore / --no-manage-gitignore, --ai-hats-dir.
"""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
        "active_role: ''\ndefault_role: ''\nlibrary_paths: []\n"
    )
    # Populate framework dir with realistic content for relocate tests.
    base = project / ".agent" / "ai-hats"
    (base / "library" / "rules").mkdir(parents=True)
    (base / "library" / "rules" / "x.md").write_text("rule x")
    (base / "tracker" / "backlog" / "tasks").mkdir(parents=True)
    (base / "STATE.md").write_text("# State\n")
    return project, CliRunner()


def _load(project):
    return yaml.safe_load((project / PROJECT_CONFIG).read_text())


def test_no_args_errors(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "set"])
    assert result.exit_code != 0
    assert "Specify at least one of" in result.output


def test_set_venv_path(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--venv", "~/.venvs/proj"])
    assert result.exit_code == 0, result.output
    assert "Updated" in result.output
    assert _load(project)["venv_path"] == "~/.venvs/proj"


def test_set_venv_idempotent(cli_project):
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "--venv", "~/.venvs/proj"])
    result = runner.invoke(main, ["config", "set", "--venv", "~/.venvs/proj"])
    assert result.exit_code == 0
    assert "unchanged" in result.output


def test_no_venv_resets_to_managed(cli_project):
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "--venv", "~/.venvs/proj"])
    result = runner.invoke(main, ["config", "set", "--no-venv"])
    assert result.exit_code == 0, result.output
    # When unset, the key is omitted from yaml (opt-in serialization).
    data = _load(project)
    assert "venv_path" not in data or data.get("venv_path") is None


def test_venv_and_no_venv_conflict(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--venv", "/x", "--no-venv"])
    assert result.exit_code != 0
    assert "Conflict" in result.output


def test_venv_invalid_path(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--venv", ""])
    assert result.exit_code != 0


def test_set_no_manage_gitignore(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--no-manage-gitignore"])
    assert result.exit_code == 0, result.output
    assert "Updated" in result.output
    assert _load(project)["manage_gitignore"] is False


def test_set_manage_gitignore_back_to_default(cli_project):
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "--no-manage-gitignore"])
    result = runner.invoke(main, ["config", "set", "--manage-gitignore"])
    assert result.exit_code == 0, result.output
    # Default is True; opt-in serialization omits it when default.
    data = _load(project)
    assert data.get("manage_gitignore", True) is True


def test_manage_gitignore_idempotent(cli_project):
    _, runner = cli_project
    # Default is already True
    result = runner.invoke(main, ["config", "set", "--manage-gitignore"])
    assert result.exit_code == 0
    assert "unchanged" in result.output


def test_ai_hats_dir_relocate(cli_project):
    project, runner = cli_project
    (project / ".gitignore").write_text(".agent/ai-hats/\n")

    result = runner.invoke(main, ["config", "set", "--ai-hats-dir", ".foo"])
    assert result.exit_code == 0, result.output
    assert "Relocated" in result.output

    # Files moved
    assert (project / ".foo" / "library" / "rules" / "x.md").exists()
    assert (project / ".foo" / "STATE.md").exists()
    assert not (project / ".agent" / "ai-hats").exists()

    # yaml updated
    assert _load(project)["ai_hats_dir"] == ".foo"

    # .gitignore swapped
    gi = (project / ".gitignore").read_text()
    assert ".foo/" in gi
    assert ".agent/ai-hats/" not in gi


def test_ai_hats_dir_invalid(cli_project):
    _, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--ai-hats-dir", "../escape"])
    assert result.exit_code != 0
    assert "Error" in result.output


def test_ai_hats_dir_noop(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["config", "set", "--ai-hats-dir", ".agent/ai-hats"])
    assert result.exit_code == 0
    assert "unchanged" in result.output


def test_combined_flags(cli_project):
    project, runner = cli_project
    result = runner.invoke(
        main,
        [
            "config",
            "set",
            "--venv",
            "~/.venvs/proj",
            "--no-manage-gitignore",
        ],
    )
    assert result.exit_code == 0, result.output
    data = _load(project)
    assert data["venv_path"] == "~/.venvs/proj"
    assert data["manage_gitignore"] is False
