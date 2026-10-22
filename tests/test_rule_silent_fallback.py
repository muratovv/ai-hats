"""The silent-fallback rule left the prompt. The hook refuses the edit."""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ComponentConfig
from ai_hats.surfaces.claude.provider import ClaudeSurface

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def test_the_rule_file_is_gone() -> None:
    assert not (LIBRARY / "usage/rules/dev_rule_silent_fallback").exists()


def test_the_python_trait_carries_the_hook_not_the_rule() -> None:
    trait = ComponentConfig.from_yaml(LIBRARY / "usage/traits/dev/python/config.yaml")
    assert "dev_rule_silent_fallback" not in trait.composition.rules
    assert "silent-fallback-gate" in trait.composition.skills
    assert "dev_rule_silent_fallback" not in trait.injection


def test_a_go_role_is_not_handed_the_python_gate() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("go-dev", overlays=asm._get_overlays("go-dev"))
    composed = ClaudeSurface().build_system_prompt(result)
    assert "dev_rule_silent_fallback" not in composed
    assert "silent-fallback-gate" not in {s.name for s in result.skills}


def test_maintainer_keeps_backlog_discipline_and_drops_the_rule() -> None:
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer", overlays=asm._get_overlays("maintainer"))
    composed = ClaudeSurface().build_system_prompt(result)
    assert "### dev_rule_silent_fallback" not in composed
    assert "### rule_backlog_discipline" in composed
    assert "silent-fallback-gate" in {s.name for s in result.skills}
