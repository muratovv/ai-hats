"""The composition root reads the config once and refuses what it cannot honour —
and the two announced degradations the diagnostic and repair commands resolve
through instead; ``Project.venv`` follows the launcher's chain (HATS-1606 / HATS-1883
/ HATS-1894)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_hats.cli._entry import project_at
from ai_hats.config.project import ProjectConfig, ProjectConfigError
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats_core.layout import ProjectLayout


@pytest.fixture()
def future_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An onboarded project whose config schema is from the future."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)
    (tmp_path / ".agent" / "ai-hats").mkdir(parents=True)
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 99\nai_hats_dir: .agent/custom\n")
    return tmp_path


def test_sanctioned_reader_refuses_future_schema(future_project: Path) -> None:
    """Positive control: fail-loud-on-newer works where it is implemented."""
    with pytest.raises(ProjectConfigError):
        ProjectConfig.from_yaml(future_project / "ai-hats.yaml")


def test_project_root_refuses_future_schema_too(future_project: Path) -> None:
    """The closed bypass: no path can be taken from a config the system just
    declared unreadable — the root refuses with the SAME family as from_yaml."""
    with pytest.raises(ProjectConfigError):
        project_at(future_project, os.environ)


@pytest.fixture()
def unreadable_project(tmp_path: Path) -> Path:
    """An onboarded project whose config will not parse — the shape that reached
    `self update` as a traceback."""
    (tmp_path / ".agent" / "ai-hats").mkdir(parents=True)
    (tmp_path / "ai-hats.yaml").write_text("manage_gitignore: not-a-bool\n")
    return tmp_path


def test_lenient_anchors_at_the_start_when_nothing_resolves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`config status` answers "what am I running" before init, and says which
    directory it is answering for."""
    from ai_hats.cli._entry import resolve_project, resolve_project_lenient
    from ai_hats_core.layout import ProjectNotFoundError

    bare = tmp_path / "bare"
    bare.mkdir()

    with pytest.raises(ProjectNotFoundError):  # positive control: strict still refuses
        resolve_project(start=bare, environ={})

    project = resolve_project_lenient(start=bare, environ={})

    assert project.layout.root == bare
    assert "no ai-hats project above" in capsys.readouterr().err


def test_lenient_falls_back_to_defaults_when_the_config_will_not_load(
    unreadable_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`self update` repairs a project whose config is the broken thing — and
    names the file it could not read."""
    from ai_hats.cli._entry import resolve_project, resolve_project_lenient

    with pytest.raises(ProjectConfigError):  # positive control
        resolve_project(start=unreadable_project, environ={})

    project = resolve_project_lenient(start=unreadable_project, environ={})

    assert project.layout.root == unreadable_project
    assert project.config == ProjectConfig()
    err = capsys.readouterr().err
    assert str(unreadable_project / "ai-hats.yaml") in err
    assert "will not load" in err


# ---------- Project.venv: env (pin-scoped) > yaml > versions/current > default ----------


def _seed_version(project: Path, sha: str, *, make_dir: bool = True) -> None:
    """versions/current -> <sha>; with ``make_dir`` a usable venv sits behind it."""
    versions = ProjectLayout.at(project).versions
    versions.root.mkdir(parents=True, exist_ok=True)
    if make_dir:
        (versions.dir(sha) / "bin").mkdir(parents=True)
        (versions.dir(sha) / "bin" / "python").write_text("#!/bin/sh\n")
        versions.sentinel(sha).write_text("")
    versions.current_pointer.write_text(f"{sha}\n")


@pytest.fixture(autouse=True)
def _no_venv_env(monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)


def test_project_venv_default(tmp_path):
    """No env, no yaml, no version → <base>/.venv."""
    assert project_at(tmp_path, os.environ).venv == tmp_path / ".agent" / "ai-hats" / ".venv"


def test_project_venv_yaml_relative(tmp_path):
    """yaml.venv_path relative → resolved against the project root."""
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nvenv_path: .venv\nprovider: claude\n"
    )
    assert project_at(tmp_path, os.environ).venv == tmp_path / ".venv"


def test_project_venv_yaml_absolute(tmp_path):
    """yaml.venv_path absolute → returned as-is."""
    abs_target = tmp_path / "shared-venv"
    (tmp_path / PROJECT_CONFIG).write_text(
        f"schema_version: 4\nai_hats_dir: .agent/ai-hats\nvenv_path: {abs_target}\nprovider: claude\n"
    )
    assert project_at(tmp_path, os.environ).venv == abs_target


def test_project_venv_env_overrides_yaml(tmp_path, monkeypatch):
    """AI_HATS_VENV env beats yaml.venv_path."""
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nvenv_path: .venv\nprovider: claude\n"
    )
    override = tmp_path / "env-override"
    monkeypatch.setenv(ENV_AI_HATS_VENV, str(override))
    assert project_at(tmp_path, os.environ).venv == override


def test_project_venv_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_VENV with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_AI_HATS_VENV, "~/my-venv")
    assert project_at(tmp_path / "project", os.environ).venv == tmp_path / "my-venv"


def test_project_venv_resolves_versioned(tmp_path):
    """No env/yaml override + valid versions/current → versions/<sha>/."""
    _seed_version(tmp_path, "cafef00d")
    assert project_at(tmp_path, os.environ).venv == ProjectLayout.at(tmp_path).versions.dir(
        "cafef00d"
    )


def test_project_venv_dangling_pointer_falls_back_to_default(tmp_path):
    """Dangling versions/current → the default venv (lazy migration keeps working)."""
    _seed_version(tmp_path, "deadbeef", make_dir=False)
    assert project_at(tmp_path, os.environ).venv == ProjectLayout.at(tmp_path).default_venv


def test_project_venv_unusable_version_falls_back_to_default(tmp_path):
    """A pointer at a version without its sentinel is not followed."""
    _seed_version(tmp_path, "abc123")
    ProjectLayout.at(tmp_path).versions.sentinel("abc123").unlink()
    assert project_at(tmp_path, os.environ).venv == ProjectLayout.at(tmp_path).default_venv


def test_project_venv_env_override_beats_versions(tmp_path, monkeypatch):
    """Explicit AI_HATS_VENV wins over a valid versions/current (HATS-339 override)."""
    _seed_version(tmp_path, "cafef00d")
    override = tmp_path / "user-owned-venv"
    monkeypatch.setenv(ENV_AI_HATS_VENV, str(override))
    assert project_at(tmp_path, os.environ).venv == override
