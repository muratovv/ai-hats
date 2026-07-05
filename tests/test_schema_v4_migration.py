"""Schema v3 → v4 migration: ai_hats_dir field surfacing (HATS-316)."""

from __future__ import annotations

import pytest
import yaml

from ai_hats.models import ProjectConfig, ProjectConfigError
from ai_hats.paths import PROJECT_CONFIG


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, default_flow_style=False, allow_unicode=True))


def test_migrate_v3_yaml_writes_ai_hats_dir_to_disk(tmp_path):
    """Loading a v3 yaml triggers migration that writes ai_hats_dir explicitly."""
    yaml_path = tmp_path / PROJECT_CONFIG
    _write_yaml(yaml_path, {"schema_version": 3, "provider": "claude"})

    cfg = ProjectConfig.from_yaml(yaml_path)

    assert cfg.schema_version == 4
    assert cfg.ai_hats_dir == ".agent/ai-hats"
    # File on disk now contains the field visibly.
    on_disk = yaml.safe_load(yaml_path.read_text())
    assert on_disk["ai_hats_dir"] == ".agent/ai-hats"
    assert on_disk["schema_version"] == 4


def test_v4_yaml_explicit_value_is_respected(tmp_path):
    """An explicit ai_hats_dir already in yaml is preserved, no rewrite."""
    yaml_path = tmp_path / PROJECT_CONFIG
    _write_yaml(
        yaml_path,
        {
            "schema_version": 4,
            "ai_hats_dir": "custom/location",
            "provider": "claude",
        },
    )
    before = yaml_path.read_text()

    cfg = ProjectConfig.from_yaml(yaml_path)

    assert cfg.ai_hats_dir == "custom/location"
    assert yaml_path.read_text() == before  # no rewrite


def test_v4_yaml_missing_field_raises(tmp_path):
    """v4 yaml without ai_hats_dir is a loud error (user manually deleted it)."""
    yaml_path = tmp_path / PROJECT_CONFIG
    _write_yaml(yaml_path, {"schema_version": 4, "provider": "claude"})

    with pytest.raises(ProjectConfigError) as excinfo:
        ProjectConfig.from_yaml(yaml_path)
    assert "ai_hats_dir" in str(excinfo.value)


def test_missing_yaml_uses_bootstrap_default(tmp_path):
    """No yaml file → ProjectConfig() returns object with bootstrap default."""
    cfg = ProjectConfig.from_yaml(tmp_path / "missing.yaml")
    assert cfg.ai_hats_dir == ".agent/ai-hats"


def test_validator_normalizes_trailing_slash(tmp_path):
    """ai_hats_dir trailing slash is stripped on validation."""
    cfg = ProjectConfig(ai_hats_dir=".agent/ai-hats/")
    assert cfg.ai_hats_dir == ".agent/ai-hats"


def test_validator_rejects_absolute_path():
    with pytest.raises(Exception):  # pydantic wraps as ValidationError
        ProjectConfig(ai_hats_dir="/absolute/path")


def test_validator_rejects_parent_traversal():
    with pytest.raises(Exception):
        ProjectConfig(ai_hats_dir="../outside")


def test_validator_rejects_empty():
    with pytest.raises(Exception):
        ProjectConfig(ai_hats_dir="")


def test_validator_rejects_dot():
    with pytest.raises(Exception):
        ProjectConfig(ai_hats_dir=".")


def test_v3_with_explicit_field_does_not_overwrite(tmp_path):
    """If a v3 yaml somehow already has ai_hats_dir, migration keeps it."""
    yaml_path = tmp_path / PROJECT_CONFIG
    _write_yaml(
        yaml_path,
        {"schema_version": 3, "ai_hats_dir": "preexisting/value", "provider": "claude"},
    )
    cfg = ProjectConfig.from_yaml(yaml_path)
    assert cfg.ai_hats_dir == "preexisting/value"
    assert cfg.schema_version == 4
