"""Backlog layout contract + its integrator wiring (HATS-864, ADR-0014 P0 #2).

:func:`tracker_paths` is the ONLY sanctioned constructor of :class:`TrackerPaths`.
NOT in ``ai_hats.paths``: that package is a dependency-free leaf.
HATS-1258 moved the dataclass here so it outlives the tracker package;
HATS-1264 stripped it to the two fields consumers actually read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import state_md_path, tasks_dir


@dataclass(frozen=True)
class TrackerPaths:
    """Frozen layout contract: WHAT a backlog needs on disk, WHERE says here."""

    tasks_dir: Path
    state_md_path: Path


def tracker_paths(project_dir: Path) -> TrackerPaths:
    """Bind the project's backlog layout to integrator policy."""
    return TrackerPaths(
        tasks_dir=tasks_dir(project_dir),
        state_md_path=state_md_path(project_dir),
    )


__all__ = ["TrackerPaths", "tracker_paths"]
