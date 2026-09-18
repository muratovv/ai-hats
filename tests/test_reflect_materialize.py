"""Unit: what ``ai-hats reflect role`` hands the auditor.

The composed dir is the composition the session runs — rule and skill bodies
read from ``source_path`` (the composer eager-loads neither), overlays applied,
and the plan's trace in the manifest so a finding can be routed to the trait,
role or override layer that brought the term.
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


def test_reflect_manifest_carries_trace(tmp_path: Path) -> None:
    """The auditor routes a finding by who brought the term — a trait, the
    role, or an override layer — so the manifest carries the plan's trace in
    the shape the session record uses."""
    import yaml

    from ai_hats.surfaces.plan import TraceEntry

    composition = CompositionResult(
        name="demo-role", priorities=[], rules=[], skills=[], injections=[]
    )
    trace = (
        TraceEntry("trait-agent", "demo-role", removed_by=None),
        TraceEntry("rules::demo_rule", "trait-agent", removed_by=None),
        TraceEntry("dev::shell", "demo-role", removed_by="overrides::project"),
    )

    target_dir = _materialize_target_composition(
        tmp_path / "out", composition, "demo-role", identity="demo-role", trace=trace
    )

    manifest = yaml.safe_load((target_dir / "manifest.yaml").read_text())
    assert manifest["identity"] == "demo-role"
    assert manifest["trace"] == [
        {"term": "trait-agent", "brought_by": "demo-role", "removed_by": None},
        {"term": "rules::demo_rule", "brought_by": "trait-agent", "removed_by": None},
        {"term": "dev::shell", "brought_by": "demo-role", "removed_by": "overrides::project"},
    ]


LIBRARY_DIR = Path(__file__).resolve().parent.parent / "packages" / "ai-hats-library" / "src"
LIBRARY_DIR = LIBRARY_DIR / "ai_hats_library"


def test_reflect_audits_the_overlaid_composition(tmp_path: Path, monkeypatch) -> None:
    """The audit reads the composition the session runs: a rule a project
    overlay adds is in it, attributed to that layer, beside the role's own."""
    from ai_hats.assembler import Assembler
    from ai_hats.cli.reflect import _audit_target
    from ai_hats.models import OverlayConfig, ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)  # the preview path prefers a library found in cwd
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
        customizations={"maintainer": OverlayConfig(add_rules=["env_rule_proxmox_infra"])},
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()

    payload = _audit_target(project, "maintainer")

    assert "env_rule_proxmox_infra" in [r.name for r in payload.result.rules]
    brought = {t.term: t.brought_by for t in payload.plan.trace if t.removed_by is None}
    assert brought["rules::env_rule_proxmox_infra"] == "overrides::project"
    assert brought["ai-hats-framework"] == "maintainer"
