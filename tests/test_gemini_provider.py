"""GeminiProvider skills + prompt-channel tests (HATS-993)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.providers import GeminiProvider
from ai_hats.skills_dir import MANAGED_MARKER


@pytest.fixture
def gemini_project(tmp_path):
    """Minimal library + role composed for the gemini provider."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Quality\n"
        "composition:\n  skills: [s]\n"
        "injection: Role body.\n"
    )

    ProjectConfig(provider="gemini", library_paths=[str(lib)]).save(
        project / PROJECT_CONFIG
    )
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = asm.composer.compose("test-role")
    return project, result


def test_wrap_materializes_skills_into_gemini_skills_dir(gemini_project) -> None:
    project, result = gemini_project
    provider = GeminiProvider()

    provider.build_session_prompt(project, result, "sid-1")

    skills_dir = project / ".gemini" / "skills"
    assert (skills_dir / "s" / "SKILL.md").is_file()
    refs = json.loads((skills_dir / MANAGED_MARKER).read_text())
    assert refs == {"sid-1": ["s"]}


def test_automate_hook_materializes_and_returns_no_args(gemini_project) -> None:
    project, result = gemini_project
    provider = GeminiProvider()

    args = provider.materialize_runtime_skills(project, result, "sid-2")

    assert args == []
    assert (project / ".gemini" / "skills" / "s" / "SKILL.md").is_file()


def test_system_prompt_omits_skills_index(gemini_project) -> None:
    _, result = gemini_project

    prompt = GeminiProvider().build_system_prompt(result)

    # HATS-993: skills reach gemini via the native .gemini/skills/ registry;
    # the HATS-701 text-index is retired.
    assert "## AVAILABLE SKILLS" not in prompt


def test_gemini_skills_dir_gitignored(gemini_project) -> None:
    project, result = gemini_project
    provider = GeminiProvider()

    provider.materialize_runtime_skills(project, result, "sid-3")

    lines = (project / ".gitignore").read_text().splitlines()
    assert ".gemini/skills/" in lines
