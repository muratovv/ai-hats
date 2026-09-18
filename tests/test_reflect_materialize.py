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
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent


SKILL_BODY = "# Demo Skill\n\nThe full body that only reflect needs.\n"
RULE_BODY = "# Demo Rule\n\nThe body the prompt delivers under ## RULES.\n"


def _skill_on_disk(tmp_path: Path) -> ResolvedComponent:
    skill_dir = tmp_path / "lib" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_BODY)
    # injection="" mirrors the post-HATS-706 composer: body is lazy, not eager.
    return ResolvedComponent(
        name="demo_skill",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="",
    )


def _rule_on_disk(tmp_path: Path) -> ResolvedComponent:
    rule_dir = tmp_path / "lib" / "rules" / "demo_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text(RULE_BODY)
    return ResolvedComponent(
        name="demo_rule",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
        injection="",
    )


def test_reflect_writes_rule_body_from_source_path(tmp_path: Path) -> None:
    """The composer stopped eager-loading ``rule.md`` the same way it did for
    skills, and the rules branch of the writer kept reading ``injection`` — so
    every audit shipped 0-byte rule files while the prompt carried the bodies."""
    composition = CompositionResult(
        name="demo-role",
        priorities=[],
        rules=[_rule_on_disk(tmp_path)],
        skills=[],
        injections=[],
    )

    target_dir = _materialize_target_composition(tmp_path / "out", composition, "demo-role")

    published = target_dir / "rules" / "demo_rule.md"
    assert published.read_text() == RULE_BODY, (
        "reflect must read the rule body from source_path, as the prompt does"
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
