"""Materialization facade — single derivation point for "compose for role X".

HATS-456 (Phase 2 closure of HATS-452 ADR-0005 D1). Before this module,
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

from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

from ai_hats_core import CompositionIncompleteError, CompositionResult

from .diagnostics import Diagnostic

if TYPE_CHECKING:
    from .assembler import Assembler
    from .models import OverlayConfig


def discover_user_rules(layout: ProjectLayout) -> tuple[Path, ...]:
    """Project-authored rule files, name-sorted (HATS-1203).

    Unfiltered by design: unlike library rules there is no catalog to select
    from, so dropping a file into ``user-rules/`` IS the opt-in.
    """
    rules_dir = layout.user_rules
    if not rules_dir.is_dir():
        return ()
    return tuple(sorted(rules_dir.glob("*.md")))


def compose_for_role(
    assembler: Assembler,
    role: str,
    *,
    runtime_overlay: OverlayConfig | None = None,
    diagnostics: list[Diagnostic] | None = None,
    tolerate_lossy: bool = False,
) -> CompositionResult:
    """Compose ``role`` using the assembler's standard overlay layering.

    Single source of truth for the question "what is the
    ``CompositionResult`` for role X in this project?". Every runtime
    and pipeline consumer (``WrapRunner``, ``SubAgentRunner``,
    ``MaterializeSystemPrompt`` step, ``Assembler.set_role`` writer,
    and ancillary compose-only sites in Assembler) routes through this
    function — direct calls to ``assembler.composer.compose(...)``
    outside this module are a HATS-456 drift signal (caught by
    ``test_no_direct_compose_outside_facade``).

    **Private to this module**: every consumer names a purpose instead — see the
    six ``compose_to_*`` facades below, pinned by
    ``tests/test_no_direct_compose_outside_facade.py`` and ``docs/adr/0005``.

    Fail-closed by default: a composition that LOST declared content raises
    :class:`CompositionIncompleteError`. ``tolerate_lossy=True`` is the one
    declared way past it, and only three facades take it.
    """
    layers = assembler._get_overlays(role)
    if runtime_overlay is not None:
        layers = [*layers, runtime_overlay]
    result = assembler.composer.compose(
        role,
        overlays=layers,
        diagnostics=diagnostics,
    )
    # An empty role name DECLARES nothing, so nothing can have been lost. The
    # composer still reports `Role '' not found`, and refusing on that turned an
    # unconfigured project into a traceback where it used to name the real
    # problem (a bad `-p`, no role set).
    if role and not tolerate_lossy and result.lost:
        raise CompositionIncompleteError(role, result.lost)
    # The composer sees library_paths only, so user-rules attach
    # here — the one funnel — and reach every consumer. Discovery is delegated
    # to the assembler: this facade stays free of filesystem work.
    return result.with_user_rules(assembler.user_rules())


# ----- the six purposes (see docs/adr/0005) -----
#
# One question decides a consumer's facade: may a composition that LOST
# declared content still serve this caller? The answer is a property of the
# purpose, not of the call site — which is why a site names its purpose here
# instead of choosing a policy of its own. Seventeen free choices were how the
# git gates came to be uninstalled by a trait typo.


def compose_to_run(
    assembler: Assembler,
    role: str,
    *,
    runtime_overlay: OverlayConfig | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> CompositionResult:
    """Strict — a session composed from a lossy role runs on the wrong prompt."""
    return compose_for_role(
        assembler, role, runtime_overlay=runtime_overlay, diagnostics=diagnostics
    )


def compose_to_install(assembler: Assembler, role: str) -> CompositionResult:
    """Strict — writes on-disk state on a user's command; refuse before writing."""
    return compose_for_role(assembler, role)


def compose_to_heal(assembler: Assembler, role: str) -> CompositionResult:
    """Tolerant — ``bump`` / ``self update`` exist BECAUSE the project may be
    broken, so refusing here would make a recoverable state unrecoverable."""
    return compose_for_role(assembler, role, tolerate_lossy=True)


def compose_to_arm(
    assembler: Assembler,
    role: str,
    *,
    runtime_overlay: OverlayConfig | None = None,
) -> CompositionResult:
    """Strict — "the gate did not install" is indistinguishable from "there is
    no gate" (ADR-0019 D9 / R6), and that is this project's oldest fail-open."""
    return compose_for_role(assembler, role, runtime_overlay=runtime_overlay)


def compose_to_report(assembler: Assembler, role: str) -> CompositionResult:
    """Tolerant — a status report must SHOW the breakage, not die of it."""
    return compose_for_role(assembler, role, tolerate_lossy=True)


def compose_to_carry(
    assembler: Assembler,
    role: str,
    *,
    runtime_overlay: OverlayConfig | None = None,
) -> CompositionResult:
    """Tolerant — dropping the carry on any error would cause the very data loss
    the record exists to prevent. ``wt_carry`` warns instead."""
    return compose_for_role(assembler, role, runtime_overlay=runtime_overlay, tolerate_lossy=True)
