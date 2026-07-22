"""HATS-437 — pause-before-shared-state-write rule wiring.

Static + composed-prompt assertions:
    - Rule files exist with expected sections.
    - Rule is listed in trait-agent's composition.
    - Rule is registered as always-on (in ALWAYS_ON_RULES).
    - When composing assistant / maintainer roles against the real
      library, the rule text reaches the built system prompt.

No subprocess — pure static + composition. Companion e2e test under
tests/e2e/ exercises the actual hook scripts.
"""
from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ComponentConfig
from ai_hats.providers import ALWAYS_ON_RULES
from ai_hats.surfaces.claude.provider import ClaudeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
RULE_DIR = LIBRARY / "core/rules/rule_pause_before_shared_state_write"


def test_rule_files_exist() -> None:
    assert (RULE_DIR / "metadata.yaml").is_file()
    assert (RULE_DIR / "rule.md").is_file()


def test_rule_metadata_well_formed() -> None:
    meta = (RULE_DIR / "metadata.yaml").read_text()
    assert "name: rule_pause_before_shared_state_write" in meta
    # tags should mark this safety-critical so future audit/grep finds it.
    assert "safety" in meta


def test_rule_body_covers_irreversible_commands() -> None:
    body = (RULE_DIR / "rule.md").read_text()
    # The reversibility table must name the irreversible subset the
    # Level-3 hook also blocks — keep rule + hook in semantic lockstep.
    assert "gh pr merge" in body
    assert "git push --force" in body or "--force" in body
    # The explicit "no chaining" rule (the actual failure mode that
    # produced HYP-026/HYP-027) must be present.
    assert "chain" in body.lower()
    # Override env must be named so agents see the per-command escape
    # hatch (otherwise they may falsely treat the hook block as terminal).
    assert "AI_HATS_SHARED_STATE_ACK" in body


def test_rule_listed_in_trait_agent_composition() -> None:
    trait = ComponentConfig.from_yaml(LIBRARY / "core/traits/trait-agent/config.yaml")
    assert "rule_pause_before_shared_state_write" in trait.composition.rules


def test_rule_is_always_on() -> None:
    # ALWAYS_ON_RULES is the source-of-truth set used by both providers'
    # build_system_prompt to decide which rules ship inline (vs on-demand).
    # The shared-state-write rule must NOT be deferrable — losing it
    # mid-session would re-introduce HYP-026/HYP-027.
    assert "rule_pause_before_shared_state_write" in ALWAYS_ON_RULES


def test_rule_present_in_composed_assistant_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose(
        "assistant", overlays=asm._get_overlays("assistant")
    )
    composed = ClaudeProvider().build_system_prompt(result)
    # Section heading the provider emits per always-on rule.
    assert "### rule_pause_before_shared_state_write" in composed
    # A signal line from rule.md body.
    assert "gh pr merge" in composed


def test_rule_present_in_composed_maintainer_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose(
        "maintainer", overlays=asm._get_overlays("maintainer")
    )
    composed = ClaudeProvider().build_system_prompt(result)
    assert "### rule_pause_before_shared_state_write" in composed
    assert "gh pr merge" in composed
