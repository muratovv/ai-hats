"""Unit tests for the materialization facade (HATS-456 Phase 1.1).

Pins the facade's contract before any runtime/pipeline consumer is
migrated to it. Pure compose path — no provider build side-effects
beyond what ``ClaudeProvider.build_system_prompt`` produces in memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.composer import CompositionResult
from ai_hats.materialize import compose_for_role, materialize_system_prompt
from ai_hats.models import ProjectConfig
from ai_hats.providers import get_provider


REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "library"


@pytest.fixture
def maintainer_project(tmp_path: Path) -> Assembler:
    """Tmp project wired to this repo's real library, maintainer active."""
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / "ai-hats.yaml")
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    return asm


# --------------------------------------------------------------------- #
# compose_for_role
# --------------------------------------------------------------------- #


def test_compose_for_role_returns_composition_result(maintainer_project):
    """Happy path: returns a non-empty ``CompositionResult`` for an
    existing role with the assembler's standard overlay layering."""
    result = compose_for_role(maintainer_project, "maintainer")
    assert isinstance(result, CompositionResult)
    assert result.name == "maintainer"
    assert not result.errors, f"unexpected compose errors: {result.errors}"
    # Maintainer has multiple traits + rules + skills — sanity-check
    # the result is not the empty-composition fallback.
    assert result.merged_injection, "merged_injection empty — composition fell through"
    assert result.rules, "no rules composed for maintainer"
    assert result.skills, "no skills composed for maintainer"


def test_compose_for_role_uses_assembler_overlays(maintainer_project):
    """The facade MUST route through ``assembler._get_overlays(role)`` —
    asserts behavioural equivalence with a direct composer call. This
    is the invariant that lets us swap inline ``compose(role,
    overlays=_get_overlays(role))`` sites with ``compose_for_role`` and
    expect bit-identical output (HATS-456 Phase 1 migration safety net).
    """
    via_facade = compose_for_role(maintainer_project, "maintainer")
    via_direct = maintainer_project.composer.compose(
        "maintainer",
        overlays=maintainer_project._get_overlays("maintainer"),
    )
    # CompositionResult is frozen + has structural equality.
    assert via_facade == via_direct


def test_compose_for_role_unknown_role_surfaces_in_errors(maintainer_project):
    """Unknown role does not raise — the composer's non-fatal-error
    contract is preserved by the facade (result returned with the error
    recorded in ``result.errors``). This matches every other
    ``composer.compose`` call site in the codebase."""
    result = compose_for_role(maintainer_project, "definitely-not-a-real-role-xyz")
    assert isinstance(result, CompositionResult)
    assert any("not found" in e.lower() for e in result.errors), (
        f"expected 'not found' in errors, got: {result.errors!r}"
    )


# --------------------------------------------------------------------- #
# materialize_system_prompt
# --------------------------------------------------------------------- #


def test_materialize_system_prompt_returns_str_with_priorities_and_rules(
    maintainer_project,
):
    """Full materialization: returns the agent-visible text the
    ``ai-hats config show-prompt`` command would print."""
    provider = get_provider("claude")
    text = materialize_system_prompt(maintainer_project, "maintainer", provider)
    assert isinstance(text, str)
    # Maintainer composition produces all three canonical sections.
    assert "## PRIORITIES" in text
    assert "## RULES" in text
    assert "## AVAILABLE SKILLS" in text


def test_materialize_system_prompt_matches_inline_compose_plus_build(
    maintainer_project,
):
    """Equivalence to the inline ``compose + build_system_prompt`` pair.

    This is the bit-identity contract the Phase 0 e2e test asserts at
    the integration level — proven here at the unit level to localise
    regressions.
    """
    provider = get_provider("claude")
    via_facade = materialize_system_prompt(
        maintainer_project, "maintainer", provider
    )
    inline_result = maintainer_project.composer.compose(
        "maintainer",
        overlays=maintainer_project._get_overlays("maintainer"),
    )
    via_inline = provider.build_system_prompt(inline_result)
    assert via_facade == via_inline


def test_materialize_system_prompt_provider_is_dispatched_to(maintainer_project):
    """``provider`` parameter is actually called — verified via a spy
    around ``build_system_prompt``.

    Note: today Claude and Gemini providers happen to produce identical
    text from ``build_system_prompt`` — the asymmetry between them
    lives in ``build_session_prompt`` (file layout / env vars), not in
    the materialized text. So byte-comparing outputs of two providers
    is not a valid dispatch check; spying is.
    """
    provider = get_provider("claude")
    calls: list = []
    real_build = provider.build_system_prompt

    def spy(result):
        calls.append(result)
        return real_build(result)

    provider.build_system_prompt = spy  # type: ignore[method-assign]
    try:
        text = materialize_system_prompt(maintainer_project, "maintainer", provider)
    finally:
        provider.build_system_prompt = real_build  # type: ignore[method-assign]

    assert len(calls) == 1, f"expected 1 build_system_prompt call, got {len(calls)}"
    assert isinstance(calls[0], CompositionResult)
    assert calls[0].name == "maintainer"
    assert text, "facade returned empty text"
