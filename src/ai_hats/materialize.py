"""Materialization facade — single derivation point for "compose for role X".

HATS-456 (Phase 2 closure of HATS-452 ADR-0005 П1). Before this module,
multiple sites inlined the same ``composer.compose(role,
overlays=_get_overlays(role))`` sequence (HITL runner, sub-agent runner,
the on-disk Assembler writer, the ``MaterializeSystemPrompt`` pipeline
step, plus several compose-only sites for hooks / status / bump). They
were *accidentally* aligned today; this module makes the alignment
*structural*.

One function, :func:`compose_for_role`, is the entire facade — it wraps
``composer.compose(role, overlays=assembler._get_overlays(role))`` so
"compose for role X" has exactly one definition.

Plan deviation note. The plan (F1) proposed a second function
``materialize_system_prompt(asm, role, provider) -> str`` covering the
full compose+build pair. During Phase 1 migration we found that every
real consumer needs the intermediate ``CompositionResult`` for some
parallel concern (hooks install, audit snapshot, stats payload,
HATS-267 override). Nobody just wants the text. Per design-minimalism,
the unused function was dropped before Phase 2. If a real text-only
consumer appears later, it's a 5-line addition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult

if TYPE_CHECKING:
    from .assembler import Assembler


def compose_for_role(assembler: "Assembler", role: str) -> CompositionResult:
    """Compose ``role`` using the assembler's standard overlay layering.

    Single source of truth for the question "what is the
    ``CompositionResult`` for role X in this project?". Every runtime
    and pipeline consumer (``WrapRunner``, ``SubAgentRunner``,
    ``MaterializeSystemPrompt`` step, ``Assembler.set_role`` writer,
    and ancillary compose-only sites in Assembler) routes through this
    function — direct calls to ``assembler.composer.compose(...)``
    outside this module are a HATS-456 drift signal (caught by
    ``test_no_direct_compose_outside_facade``).

    Does not raise on missing role — the composer's non-fatal-error
    contract (``result.errors`` populated, otherwise empty result) is
    preserved. Callers that require strict semantics should inspect
    ``result.errors`` and decide locally.
    """
    return assembler.composer.compose(
        role, overlays=assembler._get_overlays(role),
    )
