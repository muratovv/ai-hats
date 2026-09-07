"""Pins for the project-resolution slice (HATS-1606, HATS-1894).

The fail-loud half, and the two announced degradations the diagnostic and
repair commands resolve through instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.config.project import ProjectConfig, ProjectConfigError


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


def test_raw_config_peek_refuses_future_schema_too(future_project: Path) -> None:
    """The closed bypass: the tracker path can no longer be taken from a config
    the system just declared unreadable — ai_hats_dir() refuses with the SAME
    family as from_yaml (the class now lives in the paths leaf)."""
    from ai_hats.paths import ai_hats_dir

    with pytest.raises(ProjectConfigError):
        ai_hats_dir(future_project)


@pytest.fixture()
def unreadable_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An onboarded project whose config will not parse — the shape that
    reached `self update` as a traceback (HATS-1894 group D)."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)
    (tmp_path / ".agent" / "ai-hats").mkdir(parents=True)
    (tmp_path / "ai-hats.yaml").write_text("manage_gitignore: not-a-bool\n")
    return tmp_path


def test_lenient_anchors_at_cwd_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`config status` answers "what am I running" before init, and says it is
    answering for this directory."""
    from ai_hats.cli._entry import resolve_project, resolve_project_lenient
    from ai_hats_core.layout import ProjectNotFoundError

    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)

    with pytest.raises(ProjectNotFoundError):  # positive control: strict still refuses
        resolve_project(environ={})

    project = resolve_project_lenient(environ={})

    assert project.layout.root == Path.cwd()
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


def test_the_venv_heuristic_degrades_when_no_project_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second resolution point inside `config status` — a best-effort
    heuristic that used to let ProjectNotFoundError escape past its OSError arm."""
    from ai_hats.cli.maintenance import _resolved_via_heuristic

    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    monkeypatch.delenv("AI_HATS_VENV", raising=False)

    assert _resolved_via_heuristic(bare / ".agent" / "ai-hats" / ".venv").startswith("default")
