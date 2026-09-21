"""Recovery collaborator contract + the pure no-op.

A domain-agnostic DI seam: a caller depends only on the ``run()`` contract and
defaults to ``NoOpRecovery`` (touches nothing); a heavier concrete recovery is
injected by whoever wires the caller. Kept in core so a package can depend on the
contract without importing the integrator that owns the concrete (ADR-0014 Phase 1).

``run()`` reports what it did as diagnostics — the caller decides the channel
(a startup banner, a session record) — so a recovery never has to print.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ai_hats_core.diagnostics import Diagnostic


class RecoveryProtocol(Protocol):
    """The collaborator contract ``SessionManager`` depends on."""

    def run(self) -> Sequence[Diagnostic]: ...


class NoOpRecovery:
    """No-op recovery — the package-pure default; touches no filesystem."""

    def run(self) -> Sequence[Diagnostic]:
        return ()


__all__ = ["NoOpRecovery", "RecoveryProtocol"]
