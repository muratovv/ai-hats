"""HATS-948 (T15) — recovery contract + no-op live in core.

RED-under-revert: observe's package-pure default (`NoOpRecovery`) and the
`RecoveryProtocol` it depends on must resolve from core alone, with no version
subsystem in the import graph.
"""

from __future__ import annotations

from ai_hats_core.recovery import NoOpRecovery, RecoveryProtocol


def test_noop_runs_and_satisfies_protocol() -> None:
    rec: RecoveryProtocol = NoOpRecovery()
    assert rec.run() is None  # pure no-op, returns None, touches nothing


def test_arbitrary_run_object_is_a_recovery() -> None:
    class _Spy:
        def __init__(self) -> None:
            self.ran = False

        def run(self) -> None:
            self.ran = True

    spy = _Spy()
    rec: RecoveryProtocol = spy
    rec.run()
    assert spy.ran
