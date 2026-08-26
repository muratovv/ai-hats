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

from ai_hats_core import CompositionResult

from ai_hats.surfaces.system_prompt import compose_sections

LOGGER = "ai_hats.surfaces.system_prompt"


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
        prompt = compose_sections(result)

    assert "## USER RULES" not in prompt
    assert "team.md" in caplog.text
