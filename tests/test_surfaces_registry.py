"""Tests for surfaces_registry API (HATS-1178)."""

from pathlib import Path
from unittest.mock import patch

from ai_hats.surfaces import Provider
from ai_hats.surfaces_registry import (
    detect_surface_presence,
    get_installed_providers,
    get_known_surfaces,
    get_surface_info,
    is_surface_installed,
)


def test_get_known_surfaces() -> None:
    surfaces = get_known_surfaces()
    assert "claude" in surfaces
    assert "agy" in surfaces
    assert "cline" in surfaces
    assert "codex" in surfaces
    assert surfaces["cline"].default_home_dirs == (".cline",)
    assert surfaces["codex"].default_home_dirs == (".codex",)


def test_get_surface_info() -> None:
    info = get_surface_info("cline")
    assert info is not None
    assert info.ep_name == "cline"

    codex = get_surface_info("codex")
    assert codex is not None
    assert codex.ep_name == "codex"

    assert get_surface_info("unknown_xyz") is None


def test_is_surface_installed() -> None:
    # claude is builtin -> installed
    assert is_surface_installed("claude") is True

    # bogus surface is not installed
    assert is_surface_installed("non_existent_surface_xyz") is False


def test_get_installed_providers() -> None:
    installed = get_installed_providers()
    assert "claude" in installed
    assert isinstance(installed["claude"], Provider)


def test_detect_surface_presence(tmp_path: Path) -> None:
    # 1. Test installed surface (claude) with directory present
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    assert detect_surface_presence("claude", home=tmp_path) is True

    # 2. Test uninstalled known surface (cline) with directory present
    with patch("ai_hats.providers.get_provider", side_effect=ValueError("Not installed")):
        assert detect_surface_presence("cline", home=tmp_path) is False
        cline_dir = tmp_path / ".cline"
        cline_dir.mkdir()
        assert detect_surface_presence("cline", home=tmp_path) is True

        assert detect_surface_presence("codex", home=tmp_path) is False
        (tmp_path / ".codex").mkdir()
        assert detect_surface_presence("codex", home=tmp_path) is True

    # 3. Test unknown surface
    assert detect_surface_presence("unknown_surface", home=tmp_path) is False
