"""Tests for composition engine."""

import pytest

from ai_hats.composer import Composer
from ai_hats.resolver import LibraryResolver
from ai_hats.models import CheckBindingError, OverlayConfig


@pytest.fixture
def library(tmp_path):
    """Create a minimal library for testing."""
    lib = tmp_path / "lib"

    # Rule 1 (in trait and role)
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Test Rule\nDo good things.")

    # Rule 2 (role-only rule)
    rule_own_dir = lib / "rules" / "own_rule"
    rule_own_dir.mkdir(parents=True)
    (rule_own_dir / "rule.md").write_text("# Own Rule\nRole-only rule.")

    # Skill
    skill_dir = lib / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill\nCan do stuff.")

    # Base trait (flat — no sub-traits)
    trait_base = lib / "traits" / "trait-base"
    trait_base.mkdir(parents=True)
    (trait_base / "config.yaml").write_text("""
name: trait-base
injection: |
  Base injection text.
""")

    # Composite trait (flat — rules only, no sub-traits)
    trait_composite = lib / "traits" / "trait-composite"
    trait_composite.mkdir(parents=True)
    (trait_composite / "config.yaml").write_text("""
name: trait-composite
composition:
  rules:
    - test_rule
injection: |
  Composite injection text.
""")

    # Role lists traits explicitly (flat)
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: test-role
priorities:
  - Quality
  - Speed
composition:
  traits:
    - trait-base
    - trait-composite
  rules:
    - test_rule
    - own_rule
  skills:
    - test_skill
injection: |
  Role injection text.
""")

    return lib


@pytest.fixture
def composer(library):
    resolver = LibraryResolver([library])
    return Composer(resolver)


@pytest.fixture
def checks_library(library):
    """HATS-1140: ``library`` plus an executable script in test_skill and a
    trait + role that bind it, so a composition carries real check rows."""
    script = library / "skills" / "test_skill" / "hooks" / "gate.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)

    (library / "traits" / "trait-checks").mkdir(parents=True)
    (library / "traits" / "trait-checks" / "config.yaml").write_text(
        "name: trait-checks\n"
        "composition:\n"
        "  skills: [test_skill]\n"
        "  apps:\n"
        "    rack:\n"
        "      tasks:\n"
        "        - {run: test_skill/hooks/gate.sh, at: ['plan->execute']}\n"
    )
    (library / "roles" / "checks-role").mkdir(parents=True)
    (library / "roles" / "checks-role" / "config.yaml").write_text(
        "name: checks-role\n"
        "composition:\n"
        "  traits: [trait-checks]\n"
        "  apps:\n"
        "    rack:\n"
        "      tasks:\n"
        "        - {run: test_skill/hooks/gate.sh, at: ['execute->review'],"
        " on_error: warn}\n"
    )
    return library


@pytest.fixture
def checks_composer(checks_library):
    return Composer(LibraryResolver([checks_library]))


def test_compose_collects_checks_traits_before_role(checks_composer, checks_library):
    """HATS-1140 R5/R6: collection is per-role over the composition, in
    composition order — traits (a flat list, not a recursive walk) then the
    role's own — and each row is fanned out to one binding per point."""
    result = checks_composer.compose("checks-role")

    assert result.errors == []
    assert [(c.declared_by, list(c.at), c.on_error) for c in result.checks] == [
        ("trait-checks", ["plan->execute"], "refuse"),
        ("checks-role", ["execute->review"], "warn"),
    ]
    script_path = checks_library / "skills" / "test_skill" / "hooks" / "gate.sh"
    assert all(c.script_path == script_path.resolve() for c in result.checks)


def test_broken_binding_raises_where_a_broken_rule_only_reports(checks_library):
    """Two defects in one library: the rule REPORTS (as a lossy error the
    facade then refuses on — HATS-1842), the binding RAISES at compose.

    The distinction outlived the fail-open that motivated it. A broken binding
    raises inside ``composer.compose`` itself, so no facade — not even a
    tolerant one — can hand a caller a composition carrying it.
    """
    (checks_library / "roles" / "checks-role" / "config.yaml").write_text(
        "name: checks-role\ncomposition:\n  rules: [ghost_rule]\n  skills: [test_skill]\n"
    )
    composer = Composer(LibraryResolver([checks_library]))

    result = composer.compose("checks-role")
    assert [str(e) for e in result.errors] == ["Rule 'ghost_rule' not found"]
    assert [e.lossy for e in result.errors] == [True], "a dropped rule IS a loss"

    # A `run:` naming no script inside a skill: the shape ai-hats still judges
    # after HATS-1545 moved every foreign grammar under its own app key.
    (checks_library / "roles" / "checks-role" / "config.yaml").write_text(
        "name: checks-role\ncomposition:\n  skills: [test_skill]\n  apps:\n"
        "    rack:\n      tasks:\n"
        "        - {run: gate.sh, at: ['plan->execute']}\n"
    )
    composer = Composer(LibraryResolver([checks_library]))

    with pytest.raises(CheckBindingError):
        composer.compose("checks-role")


def test_overlay_removed_bound_skill_warns_and_composition_survives(checks_composer, capsys):
    """ADR-0019 D6 rev 7 / D-e end-to-end: an overlay may legally drop a
    trait-brought skill (HATS-1046). The binding goes, the composition stands."""
    overlay = OverlayConfig.from_dict({"remove": {"skills": ["test_skill"]}})

    result = checks_composer.compose("checks-role", overlay=overlay)

    assert result.checks == ()
    assert result.errors == []
    assert "an overlay removed that skill" in capsys.readouterr().err


def test_compose_without_checks_yields_empty_tuple(composer):
    """No library role declares ``checks:`` today — the field must stay empty
    rather than becoming a shape change for every existing role."""
    assert composer.compose("test-role").checks == ()


def test_compose_role(composer):
    result = composer.compose("test-role")
    assert result.name == "test-role"
    assert result.priorities == ["Quality", "Speed"]
    assert len(result.errors) == 0


def test_compose_resolves_traits(composer):
    result = composer.compose("test-role")
    # base injection, composite injection, role injection
    assert len(result.injections) == 3
    assert "Base injection" in result.injections[0]
    assert "Composite injection" in result.injections[1]
    assert "Role injection" in result.injections[2]


def test_compose_deduplicates_rules(composer):
    result = composer.compose("test-role")
    # test_rule appears in both trait-composite and role, but should be deduped
    rule_names = [r.name for r in result.rules]
    assert rule_names.count("test_rule") == 1


def test_compose_resolves_skills(composer):
    result = composer.compose("test-role")
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.name == "test_skill"
    # HATS-706: skill bodies are NOT eager-loaded into injection — the only
    # consumer (reflect mode) reads the body on demand from source_path. A
    # normal session must not pay a full SKILL.md read per skill for a body it
    # never uses.
    assert skill.injection == ""
    assert "Test Skill" in (skill.source_path / "SKILL.md").read_text()


def test_compose_does_not_eager_load_rule_bodies(composer):
    # HATS-700: rule bodies are NOT eager-loaded into injection. Only the 6
    # always-on rules reach the prompt, and the provider reads their body on
    # demand from source_path; non-always-on bodies are intentionally
    # undelivered. Eager-loading every composed rule body per session was dead
    # work (~16 KB/session reaching no channel). Symmetric to HATS-706 (skills).
    result = composer.compose("test-role")
    rule = next(r for r in result.rules if r.name == "test_rule")
    assert rule.injection == ""
    assert "Do good things." in (rule.source_path / "rule.md").read_text()


def test_compose_missing_role(composer):
    result = composer.compose("nonexistent")
    assert len(result.errors) > 0
    assert "not found" in str(result.errors[0])
    assert result.errors[0].lossy, "an unresolved role loses everything"


def test_compose_merged_injection(composer):
    result = composer.compose("test-role")
    merged = result.merged_injection
    assert "Base injection" in merged
    assert "Composite injection" in merged
    assert "Role injection" in merged
    # Order: base first, then composite, then role
    assert merged.index("Base") < merged.index("Composite") < merged.index("Role")


def test_trait_with_subtraits_is_rejected(tmp_path):
    """Trait that references other traits must produce an error."""
    lib = tmp_path / "lib"

    trait_base = lib / "traits" / "trait-base"
    trait_base.mkdir(parents=True)
    (trait_base / "config.yaml").write_text("name: trait-base\ninjection: Base.\n")

    # This trait illegally includes another trait
    trait_bad = lib / "traits" / "trait-bad"
    trait_bad.mkdir(parents=True)
    (trait_bad / "config.yaml").write_text("""
name: trait-bad
composition:
  traits:
    - trait-base
injection: Bad.
""")

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: test-role
composition:
  traits:
    - trait-bad
""")

    resolver = LibraryResolver([lib])
    result = Composer(resolver).compose("test-role")

    assert any(
        "trait-bad" in str(e) and "sub-traits" in str(e) and e.lossy for e in result.errors
    ), "the `continue` below drops the whole trait subtree — that is a loss"
    # trait-bad's injection is skipped; trait-base is never included
    assert "Bad" not in result.merged_injection
    assert "Base" not in result.merged_injection


def test_trait_with_subtraits_does_not_recurse(tmp_path):
    """Sub-traits of an invalid trait must not be silently resolved."""
    lib = tmp_path / "lib"

    trait_base = lib / "traits" / "trait-base"
    trait_base.mkdir(parents=True)
    (trait_base / "config.yaml").write_text("name: trait-base\ninjection: Base.\n")

    trait_bad = lib / "traits" / "trait-bad"
    trait_bad.mkdir(parents=True)
    (trait_bad / "config.yaml").write_text("""
name: trait-bad
composition:
  traits:
    - trait-base
injection: Bad.
""")

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: test-role
composition:
  traits:
    - trait-bad
""")

    resolver = LibraryResolver([lib])
    result = Composer(resolver).compose("test-role")
    assert "Base" not in result.merged_injection


def test_compose_namespace_resolution(tmp_path):
    """Test dev::python namespace resolution."""
    lib = tmp_path / "lib"

    trait_dir = lib / "traits" / "dev" / "python"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("""
name: dev::python
injection: Python trait.
""")

    role_dir = lib / "roles" / "ns-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: ns-role
composition:
  traits:
    - dev::python
""")

    resolver = LibraryResolver([lib])
    result = Composer(resolver).compose("ns-role")
    assert "Python trait" in result.merged_injection


# -- Overlay tests --


@pytest.fixture
def overlay_library(tmp_path):
    """Library with extra components for overlay testing."""
    lib = tmp_path / "lib"

    # Rules
    for name in ("rule_a", "rule_b"):
        d = lib / "rules" / name
        d.mkdir(parents=True)
        (d / "rule.md").write_text(f"# {name}")

    # Skills
    for name in ("skill_a", "skill_b", "skill_c"):
        d = lib / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n# {name}")

    # Traits
    trait_x = lib / "traits" / "trait-x"
    trait_x.mkdir(parents=True)
    (trait_x / "config.yaml").write_text("name: trait-x\ninjection: Trait X injection.\n")

    trait_y = lib / "traits" / "trait-y"
    trait_y.mkdir(parents=True)
    (trait_y / "config.yaml").write_text("name: trait-y\ninjection: Trait Y injection.\n")

    # trait-z carries skill_c — a skill that reaches a role ONLY through a trait,
    # never the role's own composition (the trait-brought-removal case).
    trait_z = lib / "traits" / "trait-z"
    trait_z.mkdir(parents=True)
    (trait_z / "config.yaml").write_text(
        "name: trait-z\ncomposition:\n  skills:\n    - skill_c\ninjection: Trait Z injection.\n"
    )

    # Role with trait-x, rule_a, skill_a
    role_dir = lib / "roles" / "base-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: base-role
priorities:
  - Reliability
composition:
  traits:
    - trait-x
  rules:
    - rule_a
  skills:
    - skill_a
injection: |
  Base role injection.
""")

    # Role whose only skill (skill_c) arrives via a trait, not its own list.
    tsr = lib / "roles" / "trait-skill-role"
    tsr.mkdir(parents=True)
    (tsr / "config.yaml").write_text("""
name: trait-skill-role
priorities:
  - Reliability
composition:
  traits:
    - trait-z
injection: |
  Trait-skill role injection.
""")

    return lib


@pytest.fixture
def overlay_composer(overlay_library):
    resolver = LibraryResolver([overlay_library])
    return Composer(resolver)


def test_overlay_add_trait(overlay_composer):
    overlay = OverlayConfig(add_traits=["trait-y"])
    result = overlay_composer.compose("base-role", overlay=overlay)
    assert "Trait X injection" in result.merged_injection
    assert "Trait Y injection" in result.merged_injection
    assert len(result.errors) == 0


def test_overlay_remove_trait(overlay_composer):
    overlay = OverlayConfig(remove_traits=["trait-x"])
    result = overlay_composer.compose("base-role", overlay=overlay)
    assert "Trait X injection" not in result.merged_injection
    assert "Base role injection" in result.merged_injection
    assert len(result.errors) == 0


def test_overlay_add_skill(overlay_composer):
    overlay = OverlayConfig(add_skills=["skill_b"])
    result = overlay_composer.compose("base-role", overlay=overlay)
    skill_names = [s.name for s in result.skills]
    assert "skill_a" in skill_names
    assert "skill_b" in skill_names


def test_overlay_remove_skill(overlay_composer):
    overlay = OverlayConfig(remove_skills=["skill_a"])
    result = overlay_composer.compose("base-role", overlay=overlay)
    skill_names = [s.name for s in result.skills]
    assert "skill_a" not in skill_names


def test_overlay_add_and_remove(overlay_composer):
    overlay = OverlayConfig(
        add_traits=["trait-y"],
        remove_traits=["trait-x"],
        add_skills=["skill_b"],
        remove_skills=["skill_a"],
    )
    result = overlay_composer.compose("base-role", overlay=overlay)
    assert "Trait Y injection" in result.merged_injection
    assert "Trait X injection" not in result.merged_injection
    skill_names = [s.name for s in result.skills]
    assert "skill_b" in skill_names
    assert "skill_a" not in skill_names
    assert len(result.errors) == 0


def test_overlay_injection_append(overlay_composer):
    overlay = OverlayConfig(injection_append="Custom user injection.")
    result = overlay_composer.compose("base-role", overlay=overlay)
    merged = result.merged_injection
    assert "Custom user injection." in merged
    # Must come after role injection
    assert merged.index("Base role injection") < merged.index("Custom user injection")


def test_overlay_remove_nonexistent_warns(overlay_composer):
    overlay = OverlayConfig(
        remove_traits=["nonexistent-trait"], remove_skills=["nonexistent-skill"]
    )
    result = overlay_composer.compose("base-role", overlay=overlay)
    warnings = [e for e in result.errors if "Overlay" in str(e)]
    assert not any(e.lossy for e in warnings), (
        "a remove that matched nothing leaves a SUPERSET of intent — HATS-1592"
    )
    assert len(warnings) == 2
    assert any("nonexistent-trait" in str(w) for w in warnings)
    assert any("nonexistent-skill" in str(w) for w in warnings)


def test_overlay_none_is_noop(overlay_composer):
    """compose() with overlay=None should behave identically to no overlay."""
    result_no_overlay = overlay_composer.compose("base-role")
    result_none = overlay_composer.compose("base-role", overlay=None)
    assert result_no_overlay.merged_injection == result_none.merged_injection
    assert [s.name for s in result_no_overlay.skills] == [s.name for s in result_none.skills]


def test_overlay_empty_is_noop(overlay_composer):
    """Empty OverlayConfig should not change composition."""
    overlay = OverlayConfig()
    result_base = overlay_composer.compose("base-role")
    result_overlay = overlay_composer.compose("base-role", overlay=overlay)
    assert result_base.merged_injection == result_overlay.merged_injection


# -- Per-trait granularity tests (HATS-281) --


def test_compose_exposes_trait_injections(composer):
    """trait_injections maps trait name -> injection text in declaration order."""
    result = composer.compose("test-role")
    assert list(result.trait_injections.keys()) == ["trait-base", "trait-composite"]
    assert "Base injection" in result.trait_injections["trait-base"]
    assert "Composite injection" in result.trait_injections["trait-composite"]


def test_compose_role_injection_separated(composer):
    """role_injection holds the root role's own text, no trait content."""
    result = composer.compose("test-role")
    assert "Role injection" in result.role_injection
    assert "Base injection" not in result.role_injection
    assert "Composite injection" not in result.role_injection
    # Appears exactly once in merged_injection
    assert result.merged_injection.count(result.role_injection) == 1


def test_compose_overlay_injection_separated(overlay_composer):
    """overlay_injection holds the appended text and lands after role_injection in merged."""
    overlay = OverlayConfig(injection_append="Custom user injection.")
    result = overlay_composer.compose("base-role", overlay=overlay)
    assert result.overlay_injection == "Custom user injection."
    merged = result.merged_injection
    assert merged.index(result.role_injection) < merged.index(result.overlay_injection)


def test_compose_trait_injections_dedup_by_text(tmp_path):
    """Two traits with identical injection text — only the first appears in trait_injections."""
    lib = tmp_path / "lib"

    for name in ("trait-a", "trait-b"):
        d = lib / "traits" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(f"name: {name}\ninjection: Same text.\n")

    role_dir = lib / "roles" / "dup-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: dup-role
composition:
  traits:
    - trait-a
    - trait-b
""")

    result = Composer(LibraryResolver([lib])).compose("dup-role")

    assert "trait-a" in result.trait_injections
    assert "trait-b" not in result.trait_injections
    assert result.injections.count("Same text.") == 1


def test_compose_trait_with_empty_injection_excluded(tmp_path):
    """Trait with empty injection is absent from trait_injections; deps still resolved."""
    lib = tmp_path / "lib"

    rule_dir = lib / "rules" / "r1"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# r1")

    trait_dir = lib / "traits" / "trait-empty"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("""
name: trait-empty
composition:
  rules:
    - r1
injection: ""
""")

    role_dir = lib / "roles" / "empty-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: empty-role
composition:
  traits:
    - trait-empty
""")

    result = Composer(LibraryResolver([lib])).compose("empty-role")

    assert "trait-empty" not in result.trait_injections
    assert any(r.name == "r1" for r in result.rules)


def test_compose_merged_injection_byte_identical_baseline(composer):
    """Regression guard: merged_injection wire format must not drift after T1 changes."""
    result = composer.compose("test-role")
    expected = "Base injection text.\n\nComposite injection text.\n\nRole injection text."
    assert result.merged_injection == expected


def test_compose_missing_role_has_empty_structured_fields(composer):
    """The error-branch result still carries the new fields with safe defaults."""
    result = composer.compose("nonexistent")
    assert result.trait_injections == {}
    assert result.role_injection == ""
    assert result.overlay_injection == ""


def test_overlay_removes_trait_brought_skill(overlay_composer):
    # HATS-1046: an overlay must remove a skill that a composed TRAIT brings,
    # not only one in the role's own list (backlog-manager swap enabler).
    overlay = OverlayConfig(remove_skills=["skill_c"])
    result = overlay_composer.compose("trait-skill-role", overlay=overlay)
    assert "skill_c" not in [s.name for s in result.skills]
    assert result.errors == []


def test_overlay_remove_nonexistent_skill_still_errors(overlay_composer):
    # The typo-guard survives: removing a name in neither the role nor any
    # composed trait is still an error.
    overlay = OverlayConfig(remove_skills=["skill_zzz"])
    result = overlay_composer.compose("trait-skill-role", overlay=overlay)
    assert any("skill_zzz" in str(e) and not e.lossy for e in result.errors)


def test_overlay_remove_then_add_trait_brought_skill_is_reorder(overlay_composer):
    # remove + re-add of a trait-brought skill in one layer is the HATS-421
    # move-to-end reorder, NOT a removal — skill_c must survive.
    overlay = OverlayConfig(remove_skills=["skill_c"], add_skills=["skill_c"])
    result = overlay_composer.compose("trait-skill-role", overlay=overlay)
    assert "skill_c" in [s.name for s in result.skills]
    assert result.errors == []


# -- HATS-1054: the default flip — hatrack is the composed backlog manager --
# Library under test = this source tree (worktree-safe, resolved via REPO_ROOT).

from pathlib import Path  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_BASE = _REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
from ai_hats.paths.constants import LIBRARY_LAYERS  # noqa: E402

# Engine order, never a hand-kept subset — a missing layer here reads as
# "Role not found" rather than as a stale fixture (HATS-1834).
_LIB_LAYERS = [_LIB_BASE / layer for layer in LIBRARY_LAYERS]


def _real_composer() -> Composer:
    return Composer(LibraryResolver(_LIB_LAYERS))


def _all_library_roles() -> list[str]:
    return sorted(p.parent.name for p in _LIB_BASE.glob("*/roles/*/config.yaml"))


# The 11 roles that compose trait-agent — each inherits the flipped default.
_AGENT_ROLES = [
    "assistant",
    "maintainer",
    "role-curator",
    "dev-python",
    "dev-web",
    "architect",
    "sre",
    "go-dev",
    "go-dev-full",
    "judge",
    "test-agent",
]


@pytest.mark.parametrize("role", _AGENT_ROLES)
def test_agent_role_composes_hatrack_not_backlog_manager(role):
    """After the flip, every trait-agent role carries the `hatrack` skill and no
    longer carries the classic `backlog-manager` (HATS-1054 R1)."""
    skills = {s.name for s in _real_composer().compose(role).skills}
    assert "hatrack" in skills, f"{role} must compose hatrack after the flip"
    assert "backlog-manager" not in skills, f"{role} still composes backlog-manager"


@pytest.mark.parametrize("role", _AGENT_ROLES)
def test_agent_role_composes_positive_control(role):
    skills = {s.name for s in _real_composer().compose(role).skills}
    assert "hatrack" in skills, f"{role}: composition control missing"
    assert "positive-control" in skills, f"{role}: positive-control missing"


def test_positive_control_replaces_role_curator_injection():
    result = _real_composer().compose("role-curator")
    skills = {s.name for s in result.skills}
    assert "hatrack" in skills, "role-curator: composition control missing"
    assert "positive-control" in skills, "role-curator: positive-control missing"
    assert "Positive-control discipline" not in result.merged_injection


def test_no_library_role_composes_backlog_manager():
    """Acceptance criterion (HATS-1054 R1): `backlog-manager` is composed by ZERO
    roles — the single trait-agent swap is the only attachment site."""
    comp = _real_composer()
    offenders = [
        r
        for r in _all_library_roles()
        if "backlog-manager" in {s.name for s in comp.compose(r).skills}
    ]
    assert offenders == [], f"roles still composing backlog-manager: {offenders}"


def test_remove_rule_brought_by_trait(composer):
    """HATS-1456 (S2b): removing a rule brought by a trait works without error."""
    overlay = OverlayConfig(remove_rules=["test_rule"])
    result = composer.compose("test-role", overlay=overlay)
    rule_names = [r.name for r in result.rules]
    assert "test_rule" not in rule_names
    assert result.errors == []


def test_remove_nonexistent_rule_errors(composer):
    """HATS-1456 (S2b): removing a rule not in role or any trait returns an overlay error."""
    overlay = OverlayConfig(remove_rules=["non_existent_rule"])
    result = composer.compose("test-role", overlay=overlay)
    assert any(
        "Overlay: cannot remove rule 'non_existent_rule'" in str(err) and not err.lossy
        for err in result.errors
    )


def test_remove_rule_from_role_own_list(composer):
    """HATS-1456 (S2b): removing a rule declared directly on the role (not in any trait) works without error."""
    overlay = OverlayConfig(remove_rules=["own_rule"])
    result = composer.compose("test-role", overlay=overlay)
    rule_names = [r.name for r in result.rules]
    assert "own_rule" not in rule_names
    assert "test_rule" in rule_names
    assert result.errors == []


def test_remove_and_add_same_rule_reorders_to_tail(composer):
    """HATS-1456 (S2b): remove+add of the same rule in one layer reorders it to the tail."""
    overlay = OverlayConfig(remove_rules=["own_rule"], add_rules=["own_rule"])
    result = composer.compose("test-role", overlay=overlay)
    rule_names = [r.name for r in result.rules]
    assert rule_names[-1] == "own_rule"
    assert result.errors == []


# --- HATS-1545: two declarers meeting under one app key ---


def test_a_trait_and_a_role_declaring_into_one_backlog_both_survive(checks_library):
    """R3/R4. `dict.update` over `apps` would give last-wins and drop the trait's
    gate in silence; concatenation at the leaves is what keeps both. Asserted on
    the TRAIT's row, because a role-declared row would pass even with provenance
    lost — the role is what `compose()` is called with."""
    (checks_library / "roles" / "checks-role" / "config.yaml").write_text(
        "name: checks-role\n"
        "composition:\n"
        "  traits: [trait-checks]\n"
        "  apps:\n"
        "    rack:\n"
        "      tasks:\n"
        "        - {run: test_skill/hooks/gate.sh, at: ['execute->review']}\n"
    )
    composer = Composer(LibraryResolver([checks_library]))

    result = composer.compose("checks-role")

    assert result.errors == []
    assert [(c.declared_by, c.path, list(c.at)) for c in result.checks] == [
        ("trait-checks", ("tasks",), ["plan->execute"]),
        ("checks-role", ("tasks",), ["execute->review"]),
    ]


def test_a_scalar_where_a_row_belongs_is_a_typed_refusal(checks_library):
    """A leaf that is neither a row nor a container declares no gate — and a
    silent skip here is a gate the author believes is installed."""
    (checks_library / "roles" / "checks-role" / "config.yaml").write_text(
        "name: checks-role\ncomposition:\n  skills: [test_skill]\n  apps:\n    rack: nonsense\n"
    )
    composer = Composer(LibraryResolver([checks_library]))

    with pytest.raises(CheckBindingError, match="expected a row"):
        composer.compose("checks-role")
