"""The skill index block a surface appends to its prompt (ADR-0036 D5): the
bytes today's codex/opencode index produces, from the composition half alone."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ai_hats_core.layout import ProjectLayout

from ai_hats.surfaces.plan import (
    CompositionPlan,
    Hooks,
    Prompt,
    PromptBlock,
    PromptMember,
    Skill,
)
from ai_hats.surfaces.skill_index import skill_description, skill_index_block

_SKILLS = {
    "described": "---\nname: described\ndescription: does one thing\n---\n# body\n",
    "silent": "---\nname: silent\n---\n# body\n",
    "broken": "---\nname: [\n---\n# body\n",
}


def _library(tmp_path: Path) -> tuple[list[Skill], SimpleNamespace]:
    skills: list[Skill] = []
    resolved = []
    for name, document in _SKILLS.items():
        source = tmp_path / "lib" / "skills" / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(document)
        skills.append(Skill(f"skills::{name}", source, "d", document=document))
        resolved.append(SimpleNamespace(name=name, source_path=source))
    return skills, SimpleNamespace(skills=resolved)


def _composition(skills) -> CompositionPlan:
    return CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=tuple(skills),
        hooks=Hooks((), ()),
        trace=(),
    )


def test_the_block_renders_the_bytes_todays_index_appends(tmp_path: Path):
    """Read off ``Skill.document``, never off the disk — and byte-equal to
    what ``_skill_index`` reads off the disk and appends after two newlines."""
    from ai_hats.surfaces.opencode.provider import OpenCodeSurface

    skills, result = _library(tmp_path)
    layout = ProjectLayout.at(tmp_path / "proj")
    surface = OpenCodeSurface()
    skills_root = surface.session_skills_root(layout, "s")
    composition = _composition(skills)

    block = skill_index_block(composition, skills_root, surface="opencode")

    old = surface._skill_index(layout, result, "s")
    assert old, "the sample composes skills"
    surface_prompt = Prompt((*composition.prompt.blocks, block))
    assert surface_prompt.text == f"{composition.prompt.text}\n\n{old}"
    assert block.name == "AVAILABLE SKILLS"
    assert [m.name for m in block.members] == ["opencode::skill-index"]


def test_no_skills_is_no_block(tmp_path: Path):
    assert skill_index_block(_composition(()), tmp_path / "skills", surface="codex") is None


def test_the_description_comes_from_the_document_and_falls_back_to_the_name(tmp_path: Path):
    skills, _result = _library(tmp_path)
    described, silent, broken = skills
    assert skill_description(described) == "does one thing"
    assert skill_description(silent) == "silent"
    assert skill_description(broken) == "broken"
    undocumented = Skill("skills::bare", tmp_path / "bare", "d")
    assert skill_description(undocumented) == "bare"
