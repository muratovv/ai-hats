"""What recovery reports at ``create_session`` rides on the session it created."""

from __future__ import annotations

from pathlib import Path

from ai_hats_core.diagnostics import Diagnostic, Level
from ai_hats_observe import SessionManager


class _Reporting:
    def run(self) -> tuple[Diagnostic, ...]:
        return (Diagnostic(Level.NOTE, "reclaimed 2 orphaned session caches"),)


def test_recovery_diagnostics_ride_on_the_new_session(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs", recovery=_Reporting()).create_session()
    assert [d.text for d in session.startup_diagnostics] == ["reclaimed 2 orphaned session caches"]


def test_noop_recovery_leaves_the_session_with_nothing(tmp_path: Path) -> None:
    session = SessionManager(runs_dir=tmp_path / "runs", recovery=None).create_session()
    assert session.startup_diagnostics == ()
