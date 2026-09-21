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


def test_the_block_renders_the_bytes_the_index_always_had(tmp_path: Path):
    """Read off ``Skill.document``, never off the disk — and byte-equal to the
    index the builder used to append after two newlines: one heading, one
    lead sentence, one line per skill naming its mirrored ``SKILL.md``."""
    from ai_hats.surfaces.opencode.provider import OpenCodeSurface

    skills, _result = _library(tmp_path)
    layout = ProjectLayout.at(tmp_path / "proj")
    surface = OpenCodeSurface()
    skills_root = surface.session_skills_root(layout, "s")
    composition = _composition(skills)

    block = skill_index_block(composition, skills_root, surface="opencode")

    expected = "\n".join(
        [
            "## AVAILABLE SKILLS",
            "Use a skill when its description matches the task. Before using it, read the "
            "exact SKILL.md path below; resolve its relative references from that skill "
            "directory.",
            f"- **described** — does one thing (`{skills_root / 'described' / 'SKILL.md'}`)",
            f"- **silent** — silent (`{skills_root / 'silent' / 'SKILL.md'}`)",
            f"- **broken** — broken (`{skills_root / 'broken' / 'SKILL.md'}`)",
        ]
    )
    surface_prompt = Prompt((*composition.prompt.blocks, block))
    assert surface_prompt.text == f"{composition.prompt.text}\n\n{expected}"
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
