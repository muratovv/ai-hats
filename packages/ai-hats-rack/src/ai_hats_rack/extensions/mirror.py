"""Stock ``mirror-link`` reaction (HATS-1044, ADR-0017 §2/R4).

Declared on a stored-inverse kind, it converges the reverse edge on the target
card when the workspace routes a post-lock ``link-target:<kind>`` mirror event
here. Idempotent (link/unlink is a no-op when already correct), so a broken or
missing reverse edge is repaired, not duplicated. Same-backlog and cross-backlog
pairs use exactly this machinery.
"""

from __future__ import annotations

from ..dispatch import Delta, DispatchContext, Phase
from ..linked import link_on_card, unlink_on_card
from ..registry import LinksRegistry


class MirrorLinkHandler:
    """Writes/repairs the reverse edge on the target card (convergent, journaled).

    ``MIRROR`` routes it to the ``link-target:<kind>`` keys at composition;
    ``PHASE`` marks it a target-side reaction the kernel runs in a FRESH lock
    window on the target (never nested — the one-lock rule)."""

    name = "mirror-link"
    MIRROR = True
    PHASE = Phase.POST_LOCK

    def __init__(self, registry: LinksRegistry) -> None:
        self._registry = registry

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        event = ctx.event  # LinkMirrorEvent(kind=inverse, origin, target, removed)
        op = unlink_on_card if event.removed else link_on_card
        result = op(self._registry, ctx.task, event.origin, event.kind, actor=ctx.actor)
        if not result.changed:
            return None  # already convergent — the repair is a no-op
        verb = "unlinked" if event.removed else "linked"
        return Delta(work_log=(f"mirror-link {verb} {event.origin} ({event.kind})",))
