"""Tests for per-session plugin-dir materialization (HATS-307, HATS-294)."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.composer import ResolvedComponent
from ai_hats.models import ComponentType
from ai_hats.plugin_dir import materialize_plugin_dir


def _make_skill(name: str, root: Path, body: str = "") -> ResolvedComponent:
    """Build a skill source dir on disk and the matching ResolvedComponent."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body or f"---\nname: {name}\n---\n# {name}\n")
    return ResolvedComponent(
        name=name,
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection=body,
    )


def test_returns_target_plugin_dir(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    target = tmp_path / "session_cache" / "plugin"
    out = materialize_plugin_dir("test-role", [skill], tmp_path, target)
    assert out == target
    assert out.is_dir()


def test_plugin_json_shape(tmp_path: Path) -> None:
    target = tmp_path / "plugin"
    out = materialize_plugin_dir("judge-for-role", [], tmp_path, target)
    manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "ai-hats-judge-for-role"
    assert "version" in manifest


def test_copies_skill_directory(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "role-coherence-protocol",
        skills_root,
        body="---\nname: role-coherence-protocol\ndescription: x\n---\n# body\n",
    )
    out = materialize_plugin_dir("judge-for-role", [skill], tmp_path, tmp_path / "plugin")
    copied = out / "skills" / "role-coherence-protocol" / "SKILL.md"
    assert copied.exists()
    assert "role-coherence-protocol" in copied.read_text()


def test_copies_non_skill_md_assets_verbatim(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    # Drop a non-SKILL.md asset alongside; must be preserved.
    (skill.source_path / "fixture.txt").write_text("RAW_ASSET_<ai_hats_dir>")
    out = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")
    asset = out / "skills" / "alpha" / "fixture.txt"
    assert asset.exists()
    # Verbatim — placeholder must NOT be expanded in non-SKILL.md files.
    assert asset.read_text() == "RAW_ASSET_<ai_hats_dir>"


def test_expands_placeholder_in_skill_md(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "beta",
        skills_root,
        body="see <ai_hats_dir>/state for details",
    )
    out = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")
    body = (out / "skills" / "beta" / "SKILL.md").read_text()
    assert "<ai_hats_dir>" not in body
    assert ".agent/ai-hats/state" in body


def test_empty_skills_list_makes_empty_skills_dir(tmp_path: Path) -> None:
    out = materialize_plugin_dir("test-role", [], tmp_path, tmp_path / "plugin")
    skills_dir = out / "skills"
    assert skills_dir.is_dir()
    assert list(skills_dir.iterdir()) == []


def test_skips_non_directory_source_path(tmp_path: Path) -> None:
    # A ResolvedComponent whose source_path points to a file (not a dir)
    # must be skipped without raising.
    rogue = ResolvedComponent(
        name="rogue",
        component_type=ComponentType.SKILL,
        source_path=tmp_path / "missing-dir",
        injection="",
    )
    out = materialize_plugin_dir("test-role", [rogue], tmp_path, tmp_path / "plugin")
    skills_dir = out / "skills"
    assert list(skills_dir.iterdir()) == []


def test_overwrites_existing_target(tmp_path: Path) -> None:
    """HATS-294: target dir is wiped before population so the result is
    byte-stable for the same inputs (Fork E determinism contract).
    """
    target = tmp_path / "plugin"
    target.mkdir()
    (target / "leftover.txt").write_text("stale")
    materialize_plugin_dir("test-role", [], tmp_path, target)
    assert not (target / "leftover.txt").exists()
    assert (target / ".claude-plugin" / "plugin.json").exists()


def test_parallel_invocations_with_distinct_targets(tmp_path: Path) -> None:
    """Callers pass distinct targets to get isolated plugin dirs."""
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill_a = _make_skill("alpha", skills_root)
    skill_b = _make_skill("beta", tmp_path / "src2")
    out_a = materialize_plugin_dir("role-a", [skill_a], tmp_path, tmp_path / "a")
    out_b = materialize_plugin_dir("role-b", [skill_b], tmp_path, tmp_path / "b")
    assert out_a != out_b
    assert (out_a / "skills" / "alpha").is_dir()
    assert (out_b / "skills" / "beta").is_dir()
    assert not (out_a / "skills" / "beta").exists()
    assert not (out_b / "skills" / "alpha").exists()
