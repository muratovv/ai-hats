"""Backlog layout contract + its integrator wiring (HATS-864, ADR-0014 P0 #2).

:func:`tracker_paths` is the ONLY sanctioned constructor of :class:`TrackerPaths`
— it always wires ``ensure_base`` to the validated creator, so the HATS-839
phantom-tracker guard cannot be dropped by hand-building the value. NOT in
``ai_hats.paths``: that package is a dependency-free leaf.
HATS-1258 moved the dataclass here so it outlives ``ai_hats_tracker``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import ensure_ai_hats_dir, state_md_path, tasks_dir


@dataclass(frozen=True)
class TrackerPaths:
    """Frozen layout contract: WHAT a backlog needs on disk, WHERE says here."""

    tasks_dir: Path
    state_md_path: Path
    legacy_backlog_md: Path
    # None → the consumer mkdirs its injected dirs; the integrator passes the
    # validated project-root creator (paths.ensure_ai_hats_dir, HATS-839).
    ensure_base: Callable[[], Path] | None = None

    def ensure(self) -> None:
        """Run the injected base guard, or create the injected dirs bare."""
        if self.ensure_base is not None:
            self.ensure_base()
        else:
            self.tasks_dir.parent.mkdir(parents=True, exist_ok=True)


def tracker_paths(project_dir: Path) -> TrackerPaths:
    """Bind the project's backlog layout to integrator policy."""
    return TrackerPaths(
        tasks_dir=tasks_dir(project_dir),
        state_md_path=state_md_path(project_dir),
        legacy_backlog_md=project_dir / ".agent" / "backlog.md",
        ensure_base=lambda: ensure_ai_hats_dir(project_dir),
    )


__all__ = ["TrackerPaths", "tracker_paths"]
