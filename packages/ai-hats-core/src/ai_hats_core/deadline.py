"""The budget a bounded operation may spend, minted by whoever owns it (HATS-1593).

``run_hook`` has four callers, each under a different lock or none, so a
free-floating timeout constant is correct for at most one of them. The lock hands
a deadline out at acquisition and every budget is drawn through
:meth:`budget_for`, which can only shrink — so the caller never writes the
comparison that used to be wrong. Rationale: HATS-1593.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    """An absolute ceiling on ``time.monotonic()``, labelled with its owner.

    Monotonic on purpose: a wall-clock jump must not lengthen a lock's budget.
    """

    expires_at: float
    origin: str

    @classmethod
    def under_lock(cls, timeout: float, *, lock: str) -> Deadline:
        """Mint the deadline of a lock just acquired — its budget starts now."""
        return cls(time.monotonic() + timeout, f"{lock} lock ({timeout:.0f}s)")

    @classmethod
    def without_lock(cls, budget: float, *, why: str) -> Deadline:
        """Mint a deadline for work held under no lock. ``why`` names the site so
        an unlocked run stays a declaration and never an accident."""
        return cls(time.monotonic() + budget, f"unlocked: {why}")

    def remaining(self) -> float:
        """Seconds left, floored at zero."""
        return max(0.0, self.expires_at - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def budget_for(self, requested: float) -> float:
        """``requested``, capped by what is left. Only ever shrinks — which is
        what closes the ``AI_HATS_WT_HOOK_TIMEOUT_S`` override on the ceiling."""
        return max(0.0, min(requested, self.remaining()))
