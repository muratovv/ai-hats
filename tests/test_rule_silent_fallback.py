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
from ai_hats.surfaces.claude.provider import ClaudeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
#: usage/, not core/: the body is Python-specific (``except`` syntax, ruff codes),
#: and core/ ships to every consumer — a Go project would receive it too
#: (rule_core_vs_usage_split). Same placement as dev_rule_secure_coding.
RULE_DIR = LIBRARY / "usage/rules/dev_rule_silent_fallback"


def test_rule_files_exist() -> None:
    assert (RULE_DIR / "rule.md").is_file()


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


def test_rule_listed_in_the_python_trait_composition() -> None:
    trait = ComponentConfig.from_yaml(LIBRARY / "usage/traits/dev/python/config.yaml")
    assert "dev_rule_silent_fallback" in trait.composition.rules


def test_the_rule_does_not_ride_a_language_agnostic_trait() -> None:
    """The point of the move: the carrier must be the python trait, not SE mindset."""
    se_mindset = ComponentConfig.from_yaml(LIBRARY / "usage/traits/trait-se-mindset/config.yaml")

    assert "dev_rule_silent_fallback" not in se_mindset.composition.rules
    assert "dev_rule_silent_fallback" not in se_mindset.injection


def test_a_go_role_is_not_handed_a_python_rule() -> None:
    """The concrete leak this placement fixes.

    ``go-dev`` composes trait-se-mindset alongside dev::go-core, so hanging the
    rule on SE mindset shipped ``except`` / ``# noqa: S110`` guidance to a Go
    developer. Delivery is gated by the composed rule set
    (``providers.build_system_prompt`` delivers rules in result.rules),
    so the trait a rule hangs on decides who reads it.
    """
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("go-dev", overlays=asm._get_overlays("go-dev"))
    composed = ClaudeProvider().build_system_prompt(result)

    assert "dev_rule_silent_fallback" not in {r.name for r in result.rules}
    assert "dev_rule_silent_fallback" not in composed


def test_rule_present_in_composed_maintainer_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer", overlays=asm._get_overlays("maintainer"))
    composed = ClaudeProvider().build_system_prompt(result)

    assert "### dev_rule_silent_fallback" in composed
    assert "# silent-ok:" in composed
