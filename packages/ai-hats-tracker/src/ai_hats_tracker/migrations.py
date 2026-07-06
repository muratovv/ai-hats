"""Tracker migration seam (HATS-933) — core-wired, empty registry.

The step-gated runner lives in ``ai_hats_core.migrations``; this module is its
tracker-bound instance. 0.1.0 ships an EMPTY registry (only the seam — no
TaskCard-schema migrations yet), so :func:`run_pending` is always a no-op. Real
card migrations register in ``TRACKER_MIGRATIONS`` in a later increment.
"""

from __future__ import annotations

from typing import Any

from ai_hats_core.migrations import Migration
from ai_hats_core.migrations import run_pending as _run_pending

TRACKER_MIGRATIONS: list[Migration[Any]] = []


def run_pending(ctx: Any) -> int:
    """Run tracker migrations whose step exceeds ``ctx``'s; return the count.

    Empty registry ⇒ always 0. Step bindings are trivial placeholders until a
    real migration context (a persisted step counter) exists.
    """
    return _run_pending(
        ctx,
        TRACKER_MIGRATIONS,
        read_step=lambda _ctx: 0,
        set_step=lambda _ctx, _step: None,
        persist_step=lambda _ctx, _step: None,
    )
