"""The recovery contract + no-op live in core.

observe's package-pure default (`NoOpRecovery`) and the `RecoveryProtocol` it
depends on must resolve from core alone, with no version subsystem in the import
graph. A recovery reports what it did as diagnostics; the no-op reports nothing.
"""

from __future__ import annotations

from ai_hats_core.diagnostics import Diagnostic, Level
from ai_hats_core.recovery import NoOpRecovery, RecoveryProtocol


def test_noop_runs_and_reports_nothing() -> None:
    rec: RecoveryProtocol = NoOpRecovery()
    assert rec.run() == ()  # pure no-op: touches nothing, reports nothing


def test_a_recovery_reports_its_diagnostics() -> None:
    class _Spy:
        def run(self) -> tuple[Diagnostic, ...]:
            return (Diagnostic(Level.NOTE, "reclaimed one thing"),)

    rec: RecoveryProtocol = _Spy()
    assert [d.text for d in rec.run()] == ["reclaimed one thing"]
