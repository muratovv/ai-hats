"""Tests for composition engine."""

import pytest

from ai_hats.composer import Composer
from ai_hats.resolver import LibraryResolver
from ai_hats.models import OverlayConfig


@pytest.fixture
def library(tmp_path):
    """Create a minimal library for testing."""
    lib = tmp_path / "lib"

    # Rule
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Test Rule\nDo good things.")
    (rule_dir / "metadata.yaml").write_text("name: test_rule\n")

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
    assert result.skills[0].name == "test_skill"
    assert "Test Skill" in result.skills[0].injection


def test_compose_missing_role(composer):
    result = composer.compose("nonexistent")
    assert len(result.errors) > 0
    assert "not found" in result.errors[0]


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

    assert any("trait-bad" in e and "sub-traits" in e for e in result.errors)
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
        (d / "metadata.yaml").write_text(f"name: {name}\n")

    # Skills
    for name in ("skill_a", "skill_b"):
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
    warnings = [e for e in result.errors if "Overlay" in e]
    assert len(warnings) == 2
    assert any("nonexistent-trait" in w for w in warnings)
    assert any("nonexistent-skill" in w for w in warnings)


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
    (rule_dir / "metadata.yaml").write_text("name: r1\n")

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
