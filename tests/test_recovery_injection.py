"""HATS-948 (T15) — the recovery cut: observe no-op default + integrator inject.

RED-under-revert:
- flipping ``SessionManager``'s default back to a concrete ``EnvironmentRecovery``
  (re-coupling observe to the version subsystem) fails ``test_default_is_noop``;
- dropping the ``EnvironmentRecovery`` injection from ``make_session_manager``
  (silently disabling version GC on every run) fails ``test_factory_injects_real``.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core.recovery import NoOpRecovery
from ai_hats.environment_recovery import EnvironmentRecovery
from ai_hats.composition_seam import make_session_manager
from ai_hats_observe import SessionManager
from ai_hats.paths import runs_dir


def test_default_is_noop(tmp_path: Path) -> None:
    """Package-pure default: no version subsystem in the recovery collaborator."""
    mgr = SessionManager(runs_dir=runs_dir(tmp_path))
    assert isinstance(mgr._recovery, NoOpRecovery)


def test_factory_injects_real(tmp_path: Path) -> None:
    """The run-path seam wires the real recovery so create_session GC still fires."""
    mgr = make_session_manager(tmp_path)
    assert isinstance(mgr._recovery, EnvironmentRecovery)
