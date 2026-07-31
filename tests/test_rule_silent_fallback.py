"""HATS-1373 — silent-fallback rule wiring.

Mirrors tests/test_rule_pause_before_shared_state_write.py: files exist, the
rule is attached to a trait, it is registered always-on, and its body actually
reaches a composed system prompt. A rule the agent never reads cannot prevent
the class the gate is ratcheting.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ComponentConfig
from ai_hats.providers import ALWAYS_ON_RULES
from ai_hats.surfaces.claude.provider import ClaudeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
RULE_DIR = LIBRARY / "core/rules/dev_rule_silent_fallback"


def test_rule_files_exist() -> None:
    assert (RULE_DIR / "metadata.yaml").is_file()
    assert (RULE_DIR / "rule.md").is_file()


def test_rule_metadata_well_formed() -> None:
    meta = (RULE_DIR / "metadata.yaml").read_text()
    assert "name: dev_rule_silent_fallback" in meta
    assert "error-handling" in meta


def test_rule_body_draws_the_distinction_the_gate_relies_on() -> None:
    body = (RULE_DIR / "rule.md").read_text()
    # The marker the checker actually honours.
    assert "# silent-ok:" in body
    # Breadth-vs-silence is the whole point; a body that omits it teaches the
    # opposite lesson ("never catch broadly") the measurement disproved.
    assert "noqa: BLE001" in body, "must say what a noqa does NOT justify"
    # All three reporting shapes, since a logger-only rule reintroduces the
    # false positives that sank the first definition.
    assert "domain call" in body
    assert "returned value" in body or "so does a value" in body


def test_rule_states_what_the_gate_cannot_prove() -> None:
    """The ratchet-honesty precedent: never claim more than the check verifies."""
    body = (RULE_DIR / "rule.md").read_text()
    assert "cannot prove" in body


def test_rule_listed_in_trait_se_mindset_composition() -> None:
    trait = ComponentConfig.from_yaml(LIBRARY / "usage/traits/trait-se-mindset/config.yaml")
    assert "dev_rule_silent_fallback" in trait.composition.rules


def test_rule_is_always_on() -> None:
    assert "dev_rule_silent_fallback" in ALWAYS_ON_RULES


def test_rule_present_in_composed_maintainer_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer", overlays=asm._get_overlays("maintainer"))
    composed = ClaudeProvider().build_system_prompt(result)

    assert "### dev_rule_silent_fallback" in composed
    assert "# silent-ok:" in composed
