"""Unit: reflect's role-mirror writes skill bodies from source_path.

HATS-706. The composer no longer eager-loads each ``SKILL.md`` body into
``ResolvedComponent.injection`` (that read was dead work for every non-reflect
session). reflect mode is the *sole* consumer of a skill's body, so it must
read the body on demand from ``source_path`` — not from ``injection`` (which is
now the empty default).

This pins R3 of the plan: the published ``skills/<name>.md`` still contains the
full body even though ``injection == ""``.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.cli.reflect import _materialize_target_composition
from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType


SKILL_BODY = "# Demo Skill\n\nThe full body that only reflect needs.\n"


def _skill_on_disk(tmp_path: Path) -> ResolvedComponent:
    skill_dir = tmp_path / "lib" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_BODY)
    # injection="" mirrors the post-HATS-706 composer: body is lazy, not eager.
    return ResolvedComponent(
        name="demo_skill",
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection="",
    )


def test_reflect_writes_skill_body_from_source_path(tmp_path: Path) -> None:
    skill = _skill_on_disk(tmp_path)
    composition = CompositionResult(
        name="demo-role",
        priorities=[],
        rules=[],
        skills=[skill],
        injections=[],
    )

    target_dir = _materialize_target_composition(tmp_path / "out", composition, "demo-role")

    published = target_dir / "skills" / "demo_skill.md"
    assert published.read_text() == SKILL_BODY, (
        "reflect must read the skill body from source_path, not from the "
        "(now empty) injection field"
    )
