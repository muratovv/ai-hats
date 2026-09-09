"""Backlog layout contract + its integrator wiring (HATS-864, ADR-0014 P0 #2).

:func:`tracker_paths` is the ONLY sanctioned constructor of :class:`TrackerPaths`.
NOT in ``ai_hats.paths``: that package is a dependency-free leaf.
HATS-1258 moved the dataclass here so it outlives the tracker package;
HATS-1264 stripped it to the two fields consumers actually read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import ProjectLayout


@dataclass(frozen=True)
class TrackerPaths:
    """Frozen layout contract: WHAT a backlog needs on disk, WHERE says here."""

    tasks_dir: Path
    state_md_path: Path


def tracker_paths(layout: ProjectLayout) -> TrackerPaths:
    """Bind the project's backlog layout to integrator policy."""
    return TrackerPaths(
        tasks_dir=layout.tracker.tasks_dir,
        state_md_path=layout.state_md,
    )


__all__ = ["TrackerPaths", "tracker_paths"]
