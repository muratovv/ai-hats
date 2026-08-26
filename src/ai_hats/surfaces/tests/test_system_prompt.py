"""The area's own tests for the shared system-prompt assembly (ADR-0026 D5/D7).

They reach names under ``ai_hats.surfaces`` that the facade does not export, which is
what an area test is for — ``tests/test_area_boundary.py`` forbids exactly that to
everyone else. They lived in ``tests/test_provider_session_prompt.py`` until HATS-1826
and reached in from outside, where nothing was watching.

HATS-813: the skill-index description lookup parses real YAML, keeps its name fallback,
and never crashes a prompt build on a malformed frontmatter block.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

from ai_hats.surfaces.system_prompt import compose_sections, extract_frontmatter_description

LOGGER = "ai_hats.surfaces.system_prompt"


def _skill_on_disk(tmp_path: Path, name: str, skill_md: str) -> ResolvedComponent:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def test_extract_description_reads_frontmatter(tmp_path):
    skill = _skill_on_disk(tmp_path, "doc", "---\ndescription: the doc skill\n---\n# body\n")
    assert extract_frontmatter_description(skill) == "the doc skill"


def test_extract_description_malformed_warns_then_falls_back(tmp_path, caplog):
    """A broken frontmatter block must not raise on the prompt-build path — but
    the malformed state is logged (observable), NOT silently collapsed into the
    same path as a skill that merely declares no description."""
    skill = _skill_on_disk(tmp_path, "broken", "---\nbad: : indent\n---\nbody\n")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert extract_frontmatter_description(skill) == "broken"
    assert "malformed" in caplog.text
    assert "broken" in caplog.text


def test_extract_description_absent_key_is_silent(tmp_path, caplog):
    """The contrast: a valid block with no description falls back to the name
    WITHOUT a warning — only the malformed state is noisy."""
    skill = _skill_on_disk(tmp_path, "quiet", "---\nname: quiet\n---\nbody\n")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert extract_frontmatter_description(skill) == "quiet"
    assert caplog.text == ""


def test_extract_description_missing_falls_back_to_name(tmp_path):
    skill = ResolvedComponent(
        name="ghost",
        component_type=ComponentKind.SKILL,
        source_path=tmp_path / "absent",
    )
    assert extract_frontmatter_description(skill) == "ghost"


def test_unreadable_user_rule_is_skipped_and_reported(tmp_path, caplog):
    """HATS-1826: the skip used to be a bare ``continue``.

    A user rule that cannot be read is not fatal — one bad file must not cost the
    whole prompt. But a silent skip is indistinguishable from an empty rule, so the
    branch that drops it has to say so (``dev_rule_silent_fallback``).
    """
    unreadable = tmp_path / "team.md"
    unreadable.mkdir()  # a directory where a file is expected -> IsADirectoryError
    result = CompositionResult(
        name="r", priorities=[], rules=[], skills=[], injections=[], user_rules=(unreadable,)
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        prompt = compose_sections(result, include_skills=False)

    assert "## USER RULES" not in prompt
    assert "team.md" in caplog.text
