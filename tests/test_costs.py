"""Tests for token cost estimation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.composer import Composer
from ai_hats.costs import analyze_composition, count_tokens_approx
from ai_hats.library import LibraryResolver


@pytest.fixture
def library(tmp_path):
    """Minimal library with known text sizes."""
    lib = tmp_path / "lib"

    # Rule: 30 chars
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Rule\nDo the right thing.")
    (rule_dir / "metadata.yaml").write_text("name: test_rule\n")

    # Skill: 40 chars
    skill_dir = lib / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Skill\nThis skill does important work.")

    # trait-base: injection 20 chars
    trait_base = lib / "traits" / "trait-base"
    trait_base.mkdir(parents=True)
    (trait_base / "config.yaml").write_text(
        "name: trait-base\n"
        "injection: |\n"
        "  Base injection text.\n"
    )

    # trait-with-rule: injection 30 chars + test_rule
    trait_with = lib / "traits" / "trait-with-rule"
    trait_with.mkdir(parents=True)
    (trait_with / "config.yaml").write_text(
        "name: trait-with-rule\n"
        "composition:\n"
        "  rules:\n"
        "    - test_rule\n"
        "injection: |\n"
        "  Trait with rule injection text.\n"
    )

    # Role
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-base\n"
        "    - trait-with-rule\n"
        "  skills:\n"
        "    - test_skill\n"
        "injection: |\n"
        "  Role injection text.\n"
    )

    return lib


@pytest.fixture
def composer(library):
    return Composer(LibraryResolver([library]))


# --- breakdown structure ---


def test_analyze_role_returns_all_components(composer):
    breakdown = analyze_composition(composer, "test-role")
    names = [c.name for c in breakdown.components]
    assert "trait-base" in names
    assert "trait-with-rule" in names
    assert "test-role" in names
    assert "test_rule" in names
    assert "test_skill" in names


def test_analyze_role_categories(composer):
    breakdown = analyze_composition(composer, "test-role")
    by_cat = {c.name: c.category for c in breakdown.components}
    assert by_cat["trait-base"] == "injection"
    assert by_cat["test_rule"] == "rule"
    assert by_cat["test_skill"] == "skill"
    assert by_cat["test-role"] == "injection"


def test_analyze_role_total_is_sum(composer):
    breakdown = analyze_composition(composer, "test-role")
    assert breakdown.total_tokens == sum(c.tokens for c in breakdown.components)
    assert breakdown.total_tokens > 0


def test_analyze_trait_standalone(composer):
    """Analyzing a single trait should work too."""
    breakdown = analyze_composition(composer, "trait-with-rule", as_trait=True)
    names = [c.name for c in breakdown.components]
    assert "trait-with-rule" in names
    assert "test_rule" in names
    assert breakdown.total_tokens > 0


def test_analyze_missing_role(composer):
    breakdown = analyze_composition(composer, "nonexistent")
    assert len(breakdown.errors) > 0


# --- token counting ---


def test_count_tokens_approx():
    text = "a" * 400  # 400 chars -> ~100 tokens
    assert count_tokens_approx(text) == 100


def test_count_tokens_approx_empty():
    assert count_tokens_approx("") == 0
