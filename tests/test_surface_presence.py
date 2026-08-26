"""Is a surface installed, and is it present on this host (HATS-1178, HATS-1826)?

Both answers come from the surface itself now. Until HATS-1826 a hand-kept
``surface_catalog`` carried a second copy of each surface's home directories, for
the case of a surface declared but not installed — the fold made that case
impossible for every first-party surface, and the copy had already lost
``package_name``, the one field it existed for.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.surface_registry import detect_surface_presence, is_surface_installed


def test_a_shipped_surface_is_installed() -> None:
    assert is_surface_installed("claude") is True


def test_a_name_nobody_declares_is_not_installed() -> None:
    assert is_surface_installed("non_existent_surface_xyz") is False


def test_presence_follows_the_surfaces_own_directories(tmp_path: Path) -> None:
    """The surface names its dirs; the check only looks. Claude's default is ``~/.claude``."""
    assert detect_surface_presence("claude", home=tmp_path) is False
    (tmp_path / ".claude").mkdir()
    assert detect_surface_presence("claude", home=tmp_path) is True


def test_agy_is_detected_by_either_of_its_two_homes(tmp_path: Path) -> None:
    """``detected_home_dirs`` is a list for a reason — agy answers to both names."""
    assert detect_surface_presence("agy", home=tmp_path) is False
    (tmp_path / ".gemini").mkdir()
    assert detect_surface_presence("agy", home=tmp_path) is True


def test_an_unresolvable_name_is_not_present(tmp_path: Path) -> None:
    """No surface, no directories to name — never a guess at ``~/.<name>``."""
    (tmp_path / ".unknown_surface").mkdir()
    assert detect_surface_presence("unknown_surface", home=tmp_path) is False
