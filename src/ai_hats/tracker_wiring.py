"""Integrator layout wiring for the tracker brick (HATS-864, ADR-0014 P0 #2).

The ONLY sanctioned constructor of :class:`TrackerPaths` on the integrator
side — it always wires ``ensure_base`` to the validated creator so the
HATS-839 phantom-tracker guard cannot be dropped by hand-building the value.
Mirrors ``wt_effects.py`` (HATS-866): brick declares the contract, this
module binds it to integrator policy. NOT in ``ai_hats.paths`` — that
package is a dependency-free leaf (``test_leaf_modules_are_pure``).
"""

from __future__ import annotations

from pathlib import Path

from .paths import ensure_ai_hats_dir, state_md_path, tasks_dir
from .tracker.layout import TrackerPaths


def tracker_paths(project_dir: Path) -> TrackerPaths:
    """Bind the project's tracker layout for :class:`ai_hats.state.TaskManager`."""
    return TrackerPaths(
        tasks_dir=tasks_dir(project_dir),
        state_md_path=state_md_path(project_dir),
        legacy_backlog_md=project_dir / ".agent" / "backlog.md",
        ensure_base=lambda: ensure_ai_hats_dir(project_dir),
    )


__all__ = ["tracker_paths"]
