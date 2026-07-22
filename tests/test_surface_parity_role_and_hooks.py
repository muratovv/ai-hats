"""Parity contract tests across all surfaces (claude, agy, cline).

Verifies that for every surface provider:
1. Roles are correctly threaded / propagated into session prompt.
2. Skills and their hooks are correctly materialized in the surface's native registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.providers import ClaudeProvider
from ai_hats_agy.provider import AgyProvider
from ai_hats_cline import ClineProvider
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent


@pytest.fixture
def test_skill(tmp_path: Path) -> ResolvedComponent:
    skill_dir = tmp_path / "sources" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Test skill\n---\n# My Skill Body\n")
    hooks_dir = skill_dir / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "pre_tool.sh").write_text("#!/bin/bash\necho hook\n")

    return ResolvedComponent(
        name="my-skill",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="# My Skill Body",
    )


@pytest.fixture
def composition_result(test_skill: ResolvedComponent) -> CompositionResult:
    return CompositionResult(
        name="test-role",
        priorities=["Reliability", "Cleanliness"],
        rules=[],
        skills=[test_skill],
        injections=["## ROLE INJECTION\nYou are test-role."],
        role_injection="## ROLE INJECTION\nYou are test-role.",
    )


@pytest.mark.parametrize("provider_cls", [ClaudeProvider, AgyProvider, ClineProvider])
def test_role_propagation_and_hook_materialization_parity(
    tmp_path: Path, composition_result: CompositionResult, provider_cls
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    provider = provider_cls()
    sid = f"sid-{provider.name}"

    args, env, prompt = provider.build_session_prompt(project, composition_result, sid)

    # 1. Verify role propagation: prompt contains role injection + priorities
    assert "## ROLE INJECTION" in prompt
    assert "Reliability" in prompt

    # 2. Verify skill & hook materialization
    if provider.name == "claude":
        plugin_idx = args.index("--plugin-dir")
        plugin_path = Path(args[plugin_idx + 1])
        skill_mat = plugin_path / "skills" / "my-skill" / "SKILL.md"
        hook_mat = plugin_path / "skills" / "my-skill" / "hooks" / "pre_tool.sh"
    elif provider.name == "agy":
        skill_mat = project / ".agy" / "skills" / "my-skill" / "SKILL.md"
        hook_mat = project / ".agy" / "skills" / "my-skill" / "hooks" / "pre_tool.sh"
    elif provider.name == "cline":
        skill_mat = project / ".cline" / "skills" / "my-skill" / "SKILL.md"
        hook_mat = project / ".cline" / "skills" / "my-skill" / "hooks" / "pre_tool.sh"

    assert skill_mat.is_file(), f"Skill not materialized for provider {provider.name}"
    assert hook_mat.is_file(), f"Hook script not materialized for provider {provider.name}"
