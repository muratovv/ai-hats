"""Recovery collaborator contract + the pure no-op (HATS-948, T15).

A domain-agnostic DI seam: a caller depends only on the ``run()`` contract and
defaults to ``NoOpRecovery`` (touches nothing); a heavier concrete recovery is
injected by whoever wires the caller. Kept in core so a package can depend on the
contract without importing the integrator that owns the concrete (ADR-0014 Phase 1).
"""

from __future__ import annotations

from typing import Protocol


class RecoveryProtocol(Protocol):
    """The collaborator contract ``SessionManager`` depends on."""

    def run(self) -> None: ...


class NoOpRecovery:
    """No-op recovery — the package-pure default; touches no filesystem."""

    def run(self) -> None:
        return


__all__ = ["NoOpRecovery", "RecoveryProtocol"]
