"""HATS-700 — rule-delivery contract.

The library under test is ALWAYS the source tree this test file lives in
(worktree-safe). We resolve it via ``Path(__file__).parent.parent / "library"``
and an explicit ``LibraryResolver`` — never the editable-install builtin library
(``importlib.resources.files("ai_hats.library")``), which is baked to whichever
checkout ran ``pip install -e`` and would mask worktree edits (the standard
worktree-safe library-resolution idiom used across the real-library tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.composer import Composer
from ai_hats.providers import ALWAYS_ON_RULES, ClaudeProvider, GeminiProvider
from ai_hats.resolver import LibraryResolver
from ai_hats.rule_delivery import (
    SUMMARIZED_IN_INJECTION,
    find_dangling_rule_pointers,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_LAYERS = [REPO_ROOT / "library" / "core", REPO_ROOT / "library" / "usage"]
PROVIDERS = [ClaudeProvider, GeminiProvider]


def _resolver() -> LibraryResolver:
    return LibraryResolver(LIB_LAYERS)


def _lib_text(*parts: str) -> str:
    return (REPO_ROOT / "library" / Path(*parts)).read_text()


# --------------------------------------------------------------------------- #
# Re-homing regression (step 9): the two non-always-on gaps closed by HATS-700.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("trait", ["trait-base", "trait-analyst-base"])
def test_harness_reminder_essence_delivered_in_both_base_traits(trait):
    cfg = _lib_text("core", "traits", trait, "config.yaml")
    assert "Harness Reminders" in cfg, f"{trait} missing the harness-reminder bullet"
    assert "rule_harness_reminder_hygiene" in cfg


def test_edit_efficiency_folded_into_skill_and_rule_removed():
    assert not (
        REPO_ROOT / "library" / "core" / "rules" / "dev_rule_edit_efficiency"
    ).exists(), "dev_rule_edit_efficiency rule dir should be deleted"
    assert "dev_rule_edit_efficiency" not in _lib_text(
        "core", "traits", "trait-agent", "config.yaml"
    )
    skill = _lib_text("core", "skills", "tool-call-hygiene", "SKILL.md")
    assert "Edit efficiency" in skill
    assert "3+ Edits" in skill


def test_maintainer_prompt_delivers_harness_bullet_not_edit_efficiency():
    result = Composer(_resolver()).compose("maintainer")
    assert result.errors == []
    prompt = ClaudeProvider().build_system_prompt(result)
    assert "Harness Reminders" in prompt
    assert "rule_harness_reminder_hygiene" in prompt
    assert "dev_rule_edit_efficiency" not in {r.name for r in result.rules}


# --------------------------------------------------------------------------- #
# G1 — every always-on rule's body actually reaches the prompt, for BOTH
# providers. Cases derive from ALWAYS_ON_RULES (no hand-maintained list). Guards
# the lazy read_rule_body path against a refactor that resolves the wrong
# source_path or drops the body.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize("rule_name", sorted(ALWAYS_ON_RULES))
def test_always_on_rule_body_reaches_prompt(rule_name, provider_cls):
    resolver = _resolver()
    rule_dir = resolver.resolve_rule_dir(rule_name)
    assert rule_dir is not None, f"always-on rule {rule_name} absent from library"

    rule = ResolvedComponent(
        name=rule_name,
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="g1",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )
    prompt = provider_cls().build_system_prompt(result)

    assert "## RULES" in prompt
    assert f"### {rule_name}" in prompt
    body = prompt.split(f"### {rule_name}", 1)[1]
    assert body.strip(), f"{rule_name} heading present but body empty (lazy load broke)"


# --------------------------------------------------------------------------- #
# G2 — no `see rule X` pointer reaches the agent for a rule it cannot read. The
# invariant lives in find_dangling_rule_pointers (shared by this test and the
# rule-delivery-gate pre-commit hook). This is the test that would have caught
# HATS-700.
# --------------------------------------------------------------------------- #


def test_no_dangling_rule_pointers_in_shipped_library():
    violations = find_dangling_rule_pointers(REPO_ROOT / "library")
    assert violations == [], (
        "Undelivered `see rule X` pointers — each rule must be always-on or "
        "registered in SUMMARIZED_IN_INJECTION:\n"
        + "\n".join(f"  {v.source}: see rule `{v.rule}`" for v in violations)
    )


def test_summarized_allowlist_has_no_always_on_overlap():
    # An allowlisted rule is, by definition, NOT delivered as a body; if it is
    # also always-on the registration is contradictory/stale.
    assert SUMMARIZED_IN_INJECTION.isdisjoint(ALWAYS_ON_RULES)


def test_g2_catches_an_undelivered_pointer(tmp_path):
    # Sanity: the gate is not vacuous. A trait that points at a rule which is
    # neither always-on nor allowlisted must be flagged.
    trait = tmp_path / "core" / "traits" / "trait-bad"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-bad\n"
        "injection: |\n"
        "  Do the thing — see rule `rule_totally_undelivered`.\n"
    )
    violations = find_dangling_rule_pointers(tmp_path)
    assert [v.rule for v in violations] == ["rule_totally_undelivered"]

