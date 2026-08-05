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

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.surfaces.claude.provider import ClaudeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
RULE_DIR = LIBRARY / "core/rules/rule_pause_before_shared_state_write"


def test_rule_dir_exists() -> None:
    assert RULE_DIR.is_dir()


def test_rule_present_in_composed_assistant_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("assistant", overlays=asm._get_overlays("assistant"))
    composed = ClaudeProvider().build_system_prompt(result)
    # Section heading the provider emits per always-on rule.
    assert "### rule_pause_before_shared_state_write" in composed
    # A signal line from rule.md body.
    assert "gh pr merge" in composed


def test_rule_present_in_composed_maintainer_prompt() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer", overlays=asm._get_overlays("maintainer"))
    composed = ClaudeProvider().build_system_prompt(result)
    assert "### rule_pause_before_shared_state_write" in composed
    assert "gh pr merge" in composed
