"""Tests for per-spawn plugin-dir materialization (HATS-307)."""

from __future__ import annotations

import json
import shutil
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


def test_returns_plugin_dir_under_tmp(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    out = materialize_plugin_dir("test-role", [skill], tmp_path)
    try:
        assert out.exists()
        assert out.name.startswith("ai-hats-plugin-")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_plugin_json_shape(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    out = materialize_plugin_dir("judge-for-role", [], tmp_path)
    try:
        manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == "ai-hats-judge-for-role"
        assert "version" in manifest
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_copies_skill_directory(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "role-coherence-protocol",
        skills_root,
        body="---\nname: role-coherence-protocol\ndescription: x\n---\n# body\n",
    )
    out = materialize_plugin_dir("judge-for-role", [skill], tmp_path)
    try:
        copied = out / "skills" / "role-coherence-protocol" / "SKILL.md"
        assert copied.exists()
        assert "role-coherence-protocol" in copied.read_text()
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_copies_non_skill_md_assets_verbatim(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    # Drop a non-SKILL.md asset alongside; must be preserved.
    (skill.source_path / "fixture.txt").write_text("RAW_ASSET_<ai_hats_dir>")
    out = materialize_plugin_dir("test-role", [skill], tmp_path)
    try:
        asset = out / "skills" / "alpha" / "fixture.txt"
        assert asset.exists()
        # Verbatim — placeholder must NOT be expanded in non-SKILL.md files.
        assert asset.read_text() == "RAW_ASSET_<ai_hats_dir>"
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_expands_placeholder_in_skill_md(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "beta",
        skills_root,
        body="see <ai_hats_dir>/state for details",
    )
    out = materialize_plugin_dir("test-role", [skill], tmp_path)
    try:
        body = (out / "skills" / "beta" / "SKILL.md").read_text()
        assert "<ai_hats_dir>" not in body
        assert ".agent/ai-hats/state" in body
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_empty_skills_list_makes_empty_skills_dir(tmp_path: Path) -> None:
    out = materialize_plugin_dir("test-role", [], tmp_path)
    try:
        skills_dir = out / "skills"
        assert skills_dir.is_dir()
        assert list(skills_dir.iterdir()) == []
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_skips_non_directory_source_path(tmp_path: Path) -> None:
    # A ResolvedComponent whose source_path points to a file (not a dir)
    # must be skipped without raising.
    rogue = ResolvedComponent(
        name="rogue",
        component_type=ComponentType.SKILL,
        source_path=tmp_path / "missing-dir",
        injection="",
    )
    out = materialize_plugin_dir("test-role", [rogue], tmp_path)
    try:
        skills_dir = out / "skills"
        assert list(skills_dir.iterdir()) == []
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_parallel_invocations_get_distinct_dirs(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill_a = _make_skill("alpha", skills_root)
    skill_b = _make_skill("beta", tmp_path / "src2")
    out_a = materialize_plugin_dir("role-a", [skill_a], tmp_path)
    out_b = materialize_plugin_dir("role-b", [skill_b], tmp_path)
    try:
        assert out_a != out_b
        assert (out_a / "skills" / "alpha").is_dir()
        assert (out_b / "skills" / "beta").is_dir()
        assert not (out_a / "skills" / "beta").exists()
        assert not (out_b / "skills" / "alpha").exists()
    finally:
        shutil.rmtree(out_a, ignore_errors=True)
        shutil.rmtree(out_b, ignore_errors=True)
