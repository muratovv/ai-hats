"""Recovery collaborator contract + the pure no-op (HATS-948, T15).

``SessionManager`` (``ai_hats_observe``) depends only on this ``run()`` contract
and defaults to ``NoOpRecovery`` — a package-pure default that touches nothing.
The heavy concrete ``EnvironmentRecovery`` (version GC + liveness refs) stays in
the ``ai_hats`` integrator and is injected at the compose/CLI seam. Promoting the
contract + no-op to core lets observe drop its eager integrator import
(ADR-0014 Phase 1).
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
