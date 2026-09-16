"""The skill index a surface appends to its prompt where the harness has no
skill registry of its own to consult (codex, opencode): one block naming every
mirrored skill by the ``SKILL.md`` the session writes for it (ADR-0036 D5)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.frontmatter import FrontmatterError, parse_frontmatter

from .plan import CompositionPlan, PromptBlock, PromptMember, Skill, mirror_name

BLOCK_NAME = "AVAILABLE SKILLS"

_LEAD = (
    "Use a skill when its description matches the task. Before using it, read the exact "
    "SKILL.md path below; resolve its relative references from that skill directory."
)


def skill_description(skill: Skill) -> str:
    """The frontmatter ``description`` of the document the agent reads; the
    mirror name where the document has none to give."""
    name = mirror_name(skill)
    if skill.document is None:
        return name
    try:
        metadata = parse_frontmatter(skill.document)
    except FrontmatterError:
        return name
    description = metadata.get("description")
    return description if isinstance(description, str) and description else name


def skill_index_block(
    composition: CompositionPlan, skills_root: Path, *, surface: str
) -> PromptBlock | None:
    """The block, or ``None`` for a composition without skills."""
    if not composition.skills:
        return None
    lines = [_LEAD]
    for skill in composition.skills:
        name = mirror_name(skill)
        document = skills_root / name / "SKILL.md"
        lines.append(f"- **{name}** — {skill_description(skill)} (`{document}`)")
    member = PromptMember(f"{surface}::skill-index", "\n".join(lines), None)
    return PromptBlock(BLOCK_NAME, (member,))


__all__ = ["BLOCK_NAME", "skill_description", "skill_index_block"]
