"""Script-name collisions across composed skills (HATS-993, HATS-1248)."""

from __future__ import annotations

from pathlib import Path

from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats.skills_dir import find_skill_script_collisions


def _make_skill(name: str, root: Path, body: str = "") -> ResolvedComponent:
    """Build a skill source dir on disk and the matching ResolvedComponent."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body or f"---\nname: {name}\n---\n# {name}\n")
    return ResolvedComponent(
        name=name,
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection=body,
    )


def test_two_skills_shipping_one_script_name_are_a_collision(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    s1 = _make_skill("s1", skills_root)
    (s1.source_path / "scripts").mkdir()
    (s1.source_path / "scripts" / "tool.sh").write_text("#!/bin/bash\necho s1")

    s2 = _make_skill("s2", skills_root)
    (s2.source_path / "scripts").mkdir()
    (s2.source_path / "scripts" / "tool.sh").write_text("#!/bin/bash\necho s2")

    collisions = find_skill_script_collisions([s1, s2])
    assert len(collisions) == 1
    assert "'tool.sh'" in collisions[0]
    assert "'s2'" in collisions[0]
    assert "'s1'" in collisions[0]


def test_distinct_script_names_are_no_collision(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    s1 = _make_skill("s1", skills_root)
    (s1.source_path / "scripts").mkdir()
    (s1.source_path / "scripts" / "one.sh").write_text("#!/bin/bash\n")
    s2 = _make_skill("s2", skills_root)
    (s2.source_path / "bin").mkdir()
    (s2.source_path / "bin" / "two.sh").write_text("#!/bin/bash\n")

    assert find_skill_script_collisions([s1, s2]) == []
