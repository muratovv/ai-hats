"""The pause rule left the prompt. The hook asks instead.

A composed prompt that still carries the heading means the always-on copy
came back. The sample assertion is the same render: a resolver that returned
an empty prompt would pass the absence check alone.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.surfaces.claude.provider import ClaudeSurface

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def _prompt(role: str) -> str:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose(role, overlays=asm._get_overlays(role))
    return ClaudeSurface().build_system_prompt(result)


def test_pause_rule_is_not_in_the_library() -> None:
    assert not (LIBRARY / "core/rules/rule_pause_before_shared_state_write").exists()


def test_pause_rule_is_absent_and_backlog_discipline_remains() -> None:
    for role in ("assistant", "maintainer"):
        composed = _prompt(role)
        assert "### rule_pause_before_shared_state_write" not in composed
        assert "### rule_backlog_discipline" in composed
