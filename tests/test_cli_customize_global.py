"""Tests for ``ai-hats config customize --global`` (HATS-421).

Verifies the symmetric global/project CLI behaviour:

- ``--global`` writes to ``~/.ai-hats/customizations.yaml``; default writes to project.
- ``--show --global`` / ``--show --project`` / ``--show`` produce three views.
- ``--reset --global`` clears only the global layer.
- ``--global`` + ``--project`` together is a usage error.
- File auto-creation: writing ``--global`` from a clean ``~/.ai-hats/`` creates the dir.
- Save-suppression: clearing the last overlay removes the user file.
"""

from pathlib import Path

import pytest
import yaml as _yaml
from click.testing import CliRunner

from ai_hats.cli.assembly import customize
from ai_hats.models import ProjectConfig, UserConfig


@pytest.fixture
def isolated_home(monkeypatch, tmp_path: Path):
    """Point ``Path.home()`` at a clean tmp dir so user-config writes are sandboxed."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # user_home() prefers AI_HATS_USER_HOME over Path.home() (HATS-822).
    monkeypatch.setenv("AI_HATS_USER_HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def project(monkeypatch, tmp_path: Path):
    """Minimal project dir with an ai-hats.yaml; cwd = project."""
    pdir = tmp_path / "project"
    pdir.mkdir()
    (pdir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: maintainer\n"
        "default_role: maintainer\n"
    )
    monkeypatch.chdir(pdir)
    return pdir


def _invoke(*args: str):
    runner = CliRunner()
    return runner.invoke(customize, list(args), catch_exceptions=False)


def test_reset_global_fails_friendly_when_locked(
    isolated_home: Path, project: Path, monkeypatch
):
    """HATS-526: a held lock surfaces as exit 1 + message, and the RMW never runs."""
    from functools import partial

    from ai_hats.cli import assembly
    from ai_hats_core import file_lock

    res = _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    assert res.exit_code == 0, res.output

    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    monkeypatch.setattr(assembly, "file_lock", partial(file_lock, timeout=0.1))
    with file_lock(user_path):
        res = _invoke("maintainer", "--reset", "--global")

    assert res.exit_code == 1
    assert "lock" in res.output.lower(), res.output
    cfg = UserConfig.from_yaml(user_path)
    assert cfg.customizations["maintainer"].add_traits == ["hilt-workflow"]


def test_global_write_creates_user_file(isolated_home: Path, project: Path):
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    assert not user_path.exists()
    res = _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    assert res.exit_code == 0, res.output
    assert user_path.exists()
    cfg = UserConfig.from_yaml(user_path)
    assert cfg.customizations["maintainer"].add_traits == ["hilt-workflow"]


def test_global_write_does_not_touch_project_yaml(isolated_home: Path, project: Path):
    res = _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    assert res.exit_code == 0, res.output
    proj_cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert "maintainer" not in proj_cfg.customizations


def test_project_write_does_not_touch_user_file(isolated_home: Path, project: Path):
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    res = _invoke("maintainer", "--add-trait", "debug-skill")
    assert res.exit_code == 0, res.output
    assert not user_path.exists()
    proj_cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert proj_cfg.customizations["maintainer"].add_traits == ["debug-skill"]


def test_show_global_only_renders_global_layer(isolated_home: Path, project: Path):
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-trait", "debug-skill")  # project
    res = _invoke("maintainer", "--show", "--global")
    assert res.exit_code == 0, res.output
    assert "global" in res.output
    assert "hilt-workflow" in res.output
    assert "debug-skill" not in res.output


def test_show_project_only_renders_project_layer(isolated_home: Path, project: Path):
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-trait", "debug-skill")
    res = _invoke("maintainer", "--show", "--project")
    assert res.exit_code == 0, res.output
    assert "project" in res.output
    assert "debug-skill" in res.output
    assert "hilt-workflow" not in res.output


def test_show_no_qualifier_renders_both_layers(isolated_home: Path, project: Path):
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-trait", "debug-skill")
    res = _invoke("maintainer", "--show")
    assert res.exit_code == 0, res.output
    assert "global" in res.output
    assert "project" in res.output
    assert "hilt-workflow" in res.output
    assert "debug-skill" in res.output


def test_reset_global_clears_only_global(isolated_home: Path, project: Path):
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-trait", "debug-skill")  # project
    res = _invoke("maintainer", "--reset", "--global")
    assert res.exit_code == 0, res.output

    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    if user_path.exists():
        cfg = UserConfig.from_yaml(user_path)
        assert "maintainer" not in cfg.customizations
    # else: file was deleted because no remaining customizations — equivalent
    proj_cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert proj_cfg.customizations["maintainer"].add_traits == ["debug-skill"]


def test_reset_project_clears_only_project(isolated_home: Path, project: Path):
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-trait", "debug-skill")  # project
    res = _invoke("maintainer", "--reset")
    assert res.exit_code == 0, res.output

    proj_cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert "maintainer" not in proj_cfg.customizations
    user_cfg = UserConfig.from_yaml(isolated_home / ".ai-hats" / "customizations.yaml")
    assert user_cfg.customizations["maintainer"].add_traits == ["hilt-workflow"]


def test_global_plus_project_flags_is_usage_error(isolated_home: Path, project: Path):
    res = _invoke("maintainer", "--add-trait", "X", "--global", "--project")
    assert res.exit_code != 0
    assert "mutually exclusive" in res.output.lower()


def test_global_write_no_project_yaml_still_works(isolated_home: Path, monkeypatch, tmp_path: Path):
    # Run from a directory that has NO ai-hats.yaml — global writes should not
    # require a project (you can prep your global defaults before any project).
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    res = _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    assert res.exit_code == 0, res.output
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    assert user_path.exists()


def test_clearing_last_global_overlay_removes_user_file(isolated_home: Path, project: Path):
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    _invoke("maintainer", "--add-trait", "X", "--global")
    assert user_path.exists()
    # Roll back the only customization → file should be gone.
    _invoke("maintainer", "--reset", "--global")
    assert not user_path.exists()


def test_show_global_on_empty_user_file_is_informative(isolated_home: Path, project: Path):
    res = _invoke("maintainer", "--show", "--global")
    assert res.exit_code == 0, res.output
    assert "No global customizations" in res.output


def test_yaml_round_trip_through_cli(isolated_home: Path, project: Path):
    """CLI write → file → reload → CLI inspect produces stable content."""
    _invoke("maintainer", "--add-trait", "hilt-workflow", "--global")
    _invoke("maintainer", "--add-skill", "git-mastery", "--global")
    _invoke("maintainer", "--injection-append", "extra note", "--global")
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    on_disk = _yaml.safe_load(user_path.read_text())
    assert on_disk["schema_version"] == 4
    assert "maintainer" in on_disk["customizations"]
    overlay = on_disk["customizations"]["maintainer"]
    assert "hilt-workflow" in overlay["add"]["traits"]
    assert "git-mastery" in overlay["add"]["skills"]
    assert overlay["injection_append"] == "extra note"
