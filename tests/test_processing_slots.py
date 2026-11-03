"""Candidate-only config (ADR-0034 D4): ``library.default`` does not move
``default_role``, and a key inside ``library`` that we do not own refuses."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.config.project import ProjectConfig
from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.slots import read_slots


def _project(tmp_path: Path, body: str) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "default_role: assistant\n" + body
    )
    return project


def test_library_default_stays_out_of_the_reference_role(tmp_path: Path, monkeypatch):
    project = _project(tmp_path, "library:\n  default: maintainer\n")
    cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert cfg.default_role == "assistant"
    slots = read_slots(project, cfg, [])
    assert slots.default == "maintainer"

    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    home = tmp_path / "home" / ".ai-hats"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("library:\n  paths:\n    - ~/custom\n")
    (project / ".agent" / "ai-hats").mkdir(parents=True)
    (project / ".agent" / "ai-hats" / "local.yaml").write_text("active: maintainer + worker\n")
    again = read_slots(project, cfg, [])
    assert again.active == "maintainer + worker"
    assert again.user_paths == (Path("~/custom").expanduser(),)


def test_library_is_kept_and_not_announced_as_dropped(tmp_path: Path, capsys):
    """This version reads ``library``; the unknown-field warning is for a key it does not."""
    project = _project(
        tmp_path,
        "library:\n  default: maintainer\n  paths:\n    - libs/extra\nfuture_field: 1\n",
    )
    cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    err = capsys.readouterr().err
    assert "dropping unknown field 'library'" not in err
    assert "dropping unknown field 'future_field'" in err
    assert cfg.default_role == "assistant"
    assert cfg.to_dict()["library"] == {"default": "maintainer", "paths": ["libs/extra"]}
    assert read_slots(project, cfg, []).default == "maintainer"


def test_an_unknown_key_inside_library_refuses(tmp_path: Path):
    project = _project(tmp_path, "library:\n  default: maintainer\n  apply: everywhere\n")
    cfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert cfg.default_role == "assistant"
    with pytest.raises(ProcessingError, match="unknown key 'apply'"):
        read_slots(project, cfg, [])
