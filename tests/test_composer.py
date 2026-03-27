"""Tests for composition engine."""

import pytest
from pathlib import Path

from ai_hats.composer import Composer
from ai_hats.library import LibraryResolver


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
