"""Materialization facade — single derivation point for "compose for role X".

HATS-456 (Phase 2 closure of HATS-452 ADR-0005 П1). Before this module,
four sites inlined the same ``composer.compose(role,
overlays=_get_overlays(role))`` sequence (HITL runner, sub-agent runner,
the on-disk Assembler writer, and the ``MaterializeSystemPrompt``
pipeline step). They were *accidentally* aligned today; this module
makes the alignment *structural*.

Two functions, by design:

- :func:`compose_for_role` — the compose primitive shared by all four
  runtime/pipeline consumers. Returns ``CompositionResult``.
- :func:`materialize_system_prompt` — the full compose+build pair, for
  sites that produce the final agent-visible prompt **text**
  (``MaterializeSystemPrompt`` step, Assembler's Gemini-scaffold-less
  write path). Runtime sites stop at :func:`compose_for_role` because
  their build surface (``build_session_prompt``, ``_build_meta_prompt``)
  diverges from ``build_system_prompt`` per ADR-0005 П2 (HITL vs
  Automate runtime-API axis).

No new abstraction is introduced — both functions are thin wrappers
that exist solely to put a name on the "compose for this role" / "the
text the agent sees for this role" operations so a grep across
``src/ai_hats/`` proves they are derived in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .composer import CompositionResult

if TYPE_CHECKING:
    from .assembler import Assembler
    from .providers import Provider


def compose_for_role(assembler: "Assembler", role: str) -> CompositionResult:
    """Compose ``role`` using the assembler's standard overlay layering.

    Single source of truth for the question "what is the
    ``CompositionResult`` for role X in this project?". Every runtime
    and pipeline consumer (``WrapRunner``, ``SubAgentRunner``,
    ``MaterializeSystemPrompt`` step, ``Assembler.set_role`` writer)
    routes through this function — direct calls to
    ``assembler.composer.compose(...)`` outside this module are a
    HATS-456 drift signal (caught by ``test_no_direct_compose_outside_facade``).

    Raises whatever ``Composer.compose`` raises (currently nothing —
    non-fatal compose errors surface via ``result.errors``).
    """
    return assembler.composer.compose(
        role, overlays=assembler._get_overlays(role),
    )


def materialize_system_prompt(
    assembler: "Assembler",
    role: str,
    provider: "Provider",
) -> str:
    """Full materialization: ``compose_for_role`` + ``build_system_prompt``.

    Returns the prompt text the agent of role ``role`` would see under
    ``provider``, before any provider-side wrapping (Claude session
    markers, Gemini per-session rules dir, etc.) and before path
    placeholder expansion. The same text is what ``ai-hats config
    show-prompt`` prints.

    Use this when you need the agent-visible **text** (the
    ``MaterializeSystemPrompt`` pipeline step, Assembler's on-disk
    write for providers without a scaffold template). Runtime sites
    that produce argv + env (``WrapRunner.build_session_prompt``) or a
    sub-agent meta-prompt (``SubAgentRunner._build_meta_prompt``) call
    :func:`compose_for_role` directly and feed the result into their
    own build surface — see ADR-0005 П2.
    """
    result = compose_for_role(assembler, role)
    return provider.build_system_prompt(result)
