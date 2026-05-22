"""Tests for ``UserConfig`` — user-level customizations.yaml loader (HATS-421).

Covers contract:
- Missing file → empty config (silent default).
- Malformed yaml (parse error) → ``UserConfigError`` with path up front.
- Non-mapping top-level → ``UserConfigError``.
- Unknown top-level key → ``UserConfigError`` (extra="forbid").
- Round-trip ``save`` → ``from_yaml`` preserves overlays.
- Empty config ``save`` deletes existing file (no empty stub).
- ``overlay_for`` returns ``None`` for absent / empty role overlays.
"""

from pathlib import Path

import pytest

from ai_hats.models import OverlayConfig, UserConfig, UserConfigError


def test_missing_file_returns_empty(tmp_path: Path):
    cfg = UserConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg.customizations == {}
    assert cfg.schema_version == 4


def test_malformed_yaml_raises_with_path(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    p.write_text("schema_version: 4\ncustomizations:\n  maintainer:\n    add: {traits:")
    with pytest.raises(UserConfigError) as exc:
        UserConfig.from_yaml(p)
    assert str(p) in str(exc.value), "error must surface the file path"
    assert "yaml parse error" in str(exc.value)


def test_non_mapping_top_level_raises(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    p.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(UserConfigError) as exc:
        UserConfig.from_yaml(p)
    assert "must be a mapping" in str(exc.value)


def test_unknown_top_level_key_raises(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    p.write_text("schema_version: 4\nprovider: claude\n")
    with pytest.raises(UserConfigError) as exc:
        UserConfig.from_yaml(p)
    assert "provider" in str(exc.value)


def test_valid_load(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    p.write_text(
        "schema_version: 4\n"
        "customizations:\n"
        "  maintainer:\n"
        "    add:\n"
        "      traits: [hilt-workflow]\n"
        "    injection_append: 'extra'\n"
    )
    cfg = UserConfig.from_yaml(p)
    assert "maintainer" in cfg.customizations
    overlay = cfg.customizations["maintainer"]
    assert overlay.add_traits == ["hilt-workflow"]
    assert overlay.injection_append == "extra"


def test_overlay_for_returns_none_for_absent_role(tmp_path: Path):
    cfg = UserConfig()
    assert cfg.overlay_for("nonexistent") is None


def test_overlay_for_returns_none_for_empty_overlay():
    cfg = UserConfig(customizations={"some-role": OverlayConfig()})
    # Empty overlay (no add/remove/injection) is treated as "no customization".
    assert cfg.overlay_for("some-role") is None


def test_overlay_for_returns_overlay_when_non_empty():
    overlay = OverlayConfig(add_traits=["X"])
    cfg = UserConfig(customizations={"maintainer": overlay})
    got = cfg.overlay_for("maintainer")
    assert got is overlay


def test_save_round_trip(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    cfg = UserConfig(
        customizations={
            "maintainer": OverlayConfig(
                add_traits=["hilt-workflow"], injection_append="extra"
            )
        }
    )
    cfg.save(p)
    assert p.exists()
    reloaded = UserConfig.from_yaml(p)
    assert reloaded.customizations["maintainer"].add_traits == ["hilt-workflow"]
    assert reloaded.customizations["maintainer"].injection_append == "extra"


def test_save_creates_parent_dir(tmp_path: Path):
    p = tmp_path / "nested" / "ai-hats" / "customizations.yaml"
    cfg = UserConfig(customizations={"maintainer": OverlayConfig(add_traits=["X"])})
    cfg.save(p)
    assert p.exists()
    assert p.parent.is_dir()


def test_save_empty_config_removes_file(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    # Seed with non-empty
    UserConfig(customizations={"maintainer": OverlayConfig(add_traits=["X"])}).save(p)
    assert p.exists()
    # Now save empty — file should be deleted, not left as stub
    UserConfig().save(p)
    assert not p.exists()


def test_save_all_empty_overlays_removes_file(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    UserConfig(customizations={"maintainer": OverlayConfig(add_traits=["X"])}).save(p)
    assert p.exists()
    # All overlays now empty (e.g., user removed all customizations one-by-one)
    UserConfig(customizations={"maintainer": OverlayConfig()}).save(p)
    assert not p.exists()


def test_save_skips_empty_overlays_in_yaml(tmp_path: Path):
    p = tmp_path / "customizations.yaml"
    cfg = UserConfig(
        customizations={
            "maintainer": OverlayConfig(add_traits=["X"]),
            "assistant": OverlayConfig(),  # empty
        }
    )
    cfg.save(p)
    reloaded = UserConfig.from_yaml(p)
    assert "maintainer" in reloaded.customizations
    assert "assistant" not in reloaded.customizations  # empty was dropped


def test_default_path_points_to_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".ai-hats" / "customizations.yaml"
    assert UserConfig.default_path() == expected
