"""Skill-source discovery wired into composition (HATS-871 / T11, slice 2).

A registered ``ai_hats.skills`` source root joins the resolver chain at the
shipped tier — above builtins, below user/project overrides (last-wins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats import skill_sources as ss
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved = dict(ss._SKILL_SOURCE_REGISTRY)
    ss._reset_for_tests()
    yield
    ss._reset_for_tests()
    ss._SKILL_SOURCE_REGISTRY.update(saved)


def _skill_root(base: Path, pkg: str, skill: str, body: str = "body") -> Path:
    skill_dir = base / pkg / "skills" / skill
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {skill}\n---\n{body}\n")
    return base / pkg


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    return project


def test_registered_skill_source_is_resolvable(tmp_path):
    root = _skill_root(tmp_path, "engine_pkg", "engine-skill")
    ss.register_skill_source("engine_pkg", root)

    asm = Assembler(_project(tmp_path))

    assert root in asm.library_paths
    assert asm.resolver.resolve_skill_dir("engine-skill") == root / "skills" / "engine-skill"


def test_project_override_beats_engine_skill_source(tmp_path):
    engine = _skill_root(tmp_path, "engine_pkg", "shared", body="ENGINE")
    ss.register_skill_source("engine_pkg", engine)

    projlib = tmp_path / "projlib"
    proj_skill = projlib / "skills" / "shared"
    proj_skill.mkdir(parents=True)
    (proj_skill / "SKILL.md").write_text("---\nname: shared\n---\nOVERRIDE\n")

    # library_paths=[projlib] is the highest-priority (explicit) tier.
    asm = Assembler(_project(tmp_path), library_paths=[projlib])

    assert asm.resolver.resolve_skill_dir("shared") == proj_skill


def test_engine_skill_source_outranks_builtin_tier(tmp_path):
    engine = _skill_root(tmp_path, "engine_pkg", "engine-skill")
    ss.register_skill_source("engine_pkg", engine)

    asm = Assembler(_project(tmp_path))
    layers = asm.library_paths
    # Shipped tier: engine source sits after the builtin layers (higher priority).
    assert layers.index(engine) >= 1
