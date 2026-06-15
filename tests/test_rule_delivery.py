"""HATS-700 — rule-delivery contract.

The library under test is ALWAYS the source tree this test file lives in
(worktree-safe). We resolve it via ``Path(__file__).parent.parent / "library"``
and an explicit ``LibraryResolver`` — never the editable-install builtin library
(``importlib.resources.files("ai_hats.library")``), which is baked to whichever
checkout ran ``pip install -e`` and would mask worktree edits. Same idiom as
``test_real_maintainer_trait_declares_session_start_sync_hooks``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.composer import Composer, CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, HooksConfig
from ai_hats.providers import ALWAYS_ON_RULES, ClaudeProvider, GeminiProvider
from ai_hats.resolver import LibraryResolver

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
        component_type=ComponentType.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="g1",
        priorities=[],
        rules=[rule],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    prompt = provider_cls().build_system_prompt(result)

    assert "## RULES" in prompt
    assert f"### {rule_name}" in prompt
    body = prompt.split(f"### {rule_name}", 1)[1]
    assert body.strip(), f"{rule_name} heading present but body empty (lazy load broke)"
