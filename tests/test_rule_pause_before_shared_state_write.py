"""HATS-437 — pause-before-shared-state-write rule wiring.

Static + composed-prompt assertions:
    - Rule files exist with expected sections.
    - Rule is listed in trait-agent's composition.
    - Rule directory exists.
    - When composing assistant / maintainer roles against the real
      library, the rule text reaches the built system prompt.

No subprocess — pure static + composition. Companion e2e test under
tests/e2e/ exercises the actual hook scripts.
"""

from __future__ import annotations

import re
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ComponentConfig
from ai_hats.surfaces.claude.provider import ClaudeSurface

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
RULE_DIR = LIBRARY / "core/rules/rule_pause_before_shared_state_write"


def test_rule_files_exist() -> None:
    assert (RULE_DIR / "rule.md").is_file()


#: A per-command verdict claim: a table row whose cell is `denies`/`allows`/`asks`.
_VERDICT_CLAIM = re.compile(r"\|\s*\**(denies|allows|asks)\**\s*\|", re.IGNORECASE)


def test_rule_does_not_restate_the_hooks_verdicts() -> None:
    """The rule points at the hook; it must not mirror what the hook decides.

    What stood here asserted that the strings `gh pr merge` and `--force` were
    present and called that "semantic lockstep". The table it certified promised
    `denies` for both while `test_shared_state_guard.py` — green, same run —
    pinned the hook at `ask`. A check shaped like the invariant's NAME rather
    than its content cannot see that (HATS-1825).
    """
    planted = "| `gh pr merge` | **irreversible** | **denies** |"
    assert _VERDICT_CLAIM.search(planted), "pattern misses a verdict shown to it"

    body = (RULE_DIR / "rule.md").read_text()
    hit = _VERDICT_CLAIM.search(body)
    assert hit is None, (
        f"rule.md restates a hook verdict ({hit.group(0)!r}). The hook prints its "
        f"own refusal; a copy here drifts silently."
    )


def test_rule_defers_to_the_classifier_and_names_both_consent_channels() -> None:
    """Deleting the table must not delete what the agent actually needs: which
    commands are in scope, and the two ways consent can arrive."""
    body = (RULE_DIR / "rule.md").read_text()
    assert "shared_state_classifier.sh" in body
    for verdict in ("irreversible", "gated", "shared", "safe"):
        assert f"`{verdict}`" in body, verdict
    assert "AI_HATS_SHARED_STATE_ACK" in body
    assert "chain" in body.lower()


def test_rule_listed_in_trait_agent_composition() -> None:
    trait = ComponentConfig.from_yaml(LIBRARY / "core/traits/trait-agent/config.yaml")
    assert "rule_pause_before_shared_state_write" in trait.composition.rules


def test_rule_present_in_composed_assistant_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("assistant", overlays=asm._get_overlays("assistant"))
    composed = ClaudeSurface().build_system_prompt(result)
    # Section heading the provider emits per always-on rule.
    assert "### rule_pause_before_shared_state_write" in composed
    # A signal line from rule.md body.
    assert "gh pr merge" in composed


def test_rule_present_in_composed_maintainer_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer", overlays=asm._get_overlays("maintainer"))
    composed = ClaudeSurface().build_system_prompt(result)
    assert "### rule_pause_before_shared_state_write" in composed
    assert "gh pr merge" in composed
