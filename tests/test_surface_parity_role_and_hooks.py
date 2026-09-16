"""Parity contract tests across the surfaces that mirror skills (claude, agy, cline).

Verifies that for every one of them, on the plan path:
1. Roles are correctly threaded / propagated into the session prompt.
2. Skills and their hooks are correctly mirrored in the surface's native registry.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

import pytest

from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.surfaces.cline import ClineSurface
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from tests._plan_helpers import composition_of, materialized


@pytest.fixture
def test_skill(tmp_path: Path) -> ResolvedComponent:
    skill_dir = tmp_path / "sources" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Test skill\n---\n# My Skill Body\n"
    )
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


@pytest.mark.parametrize("provider_cls", [ClaudeSurface, AgySurface, ClineSurface])
def test_role_propagation_and_hook_materialization_parity(
    tmp_path: Path, composition_result: CompositionResult, provider_cls
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout.at(project)
    provider = provider_cls()
    sid = f"sid-{provider.name}"

    plan = materialized(
        provider,
        composition_of(composition_result, layout=layout),
        layout=layout,
        root=layout.cache.session(sid),
    )

    # 1. Verify role propagation: prompt contains role injection + priorities
    assert "## ROLE INJECTION" in plan.prompt.text
    assert "Reliability" in plan.prompt.text

    # 2. Verify skill & hook mirroring where the surface says it mirrors
    skills_root = provider.session_skills_root(layout, sid)
    assert skills_root is not None
    skill_mat = skills_root / "my-skill" / "SKILL.md"
    hook_mat = skills_root / "my-skill" / "hooks" / "pre_tool.sh"
    assert skill_mat.is_file(), f"Skill not mirrored for provider {provider.name}"
    assert hook_mat.is_file(), f"Hook script not mirrored for provider {provider.name}"
    assert not (project / ".cline").exists() and not (project / ".claude").exists()  # clean root
