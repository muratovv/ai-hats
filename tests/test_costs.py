"""Tests for token cost estimation."""

from __future__ import annotations


import pytest

from ai_hats.composer import Composer
from ai_hats.costs import analyze_composition, count_tokens_approx, count_tokens_sdk
from ai_hats.resolver import LibraryResolver


@pytest.fixture
def library(tmp_path):
    """Minimal library with known text sizes."""
    lib = tmp_path / "lib"

    # Rule: 30 chars
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Rule\nDo the right thing.")
    (rule_dir / "metadata.yaml").write_text("name: test_rule\n")

    # Skill: frontmatter (name + description, always-on) + body (on-demand)
    skill_dir = lib / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: test_skill\n"
        "description: Does important work when invoked.\n"
        "---\n"
        "# Skill\nThis skill does important work."
    )

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


# --- HATS-957: always-on vs on-demand split ---


def test_split_totals_sum_to_total(composer):
    """always_on + on_demand must equal total — no double count, no loss."""
    b = analyze_composition(composer, "test-role", exact=False)
    assert b.always_on_tokens + b.on_demand_tokens == b.total_tokens


def test_skill_body_is_on_demand_not_always_on(composer):
    """A skill's body loads on demand; only its name+description stays resident."""
    b = analyze_composition(composer, "test-role", exact=False)
    skill = next(c for c in b.components if c.category == "skill")
    assert skill.on_demand_tokens > 0
    assert 0 < skill.always_on_tokens < skill.tokens
    assert skill.always_on_tokens + skill.on_demand_tokens == skill.tokens


def test_skill_description_counted_in_always_on(composer):
    """The resident slice equals count of '<name>: <description>'."""
    b = analyze_composition(composer, "test-role", exact=False)
    skill = next(c for c in b.components if c.category == "skill")
    assert skill.always_on_tokens == 11  # worked example: 45 chars // 4


def test_rules_and_injection_are_fully_always_on(composer):
    """Rule bodies and injections are inlined in the base prompt: no on-demand part."""
    b = analyze_composition(composer, "test-role", exact=False)
    for c in b.components:
        if c.category in ("rule", "injection"):
            assert c.on_demand_tokens == 0
            assert c.always_on_tokens == c.tokens


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


# --- Regression: HATS-131 / audit #12 ---


def test_count_tokens_sdk_returns_none_when_anthropic_missing(monkeypatch):
    """Regression: graceful fallback when the optional `anthropic` SDK is absent.

    `anthropic` lives in the `[costs]` extra. Projects without it should
    transparently fall back to the `len // 4` approximation rather than
    crash. Setting `sys.modules['anthropic'] = None` makes any subsequent
    `import anthropic` raise ImportError, simulating an uninstalled SDK
    even when it's actually present in the dev environment.
    """
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert count_tokens_sdk(["hello"]) is None
