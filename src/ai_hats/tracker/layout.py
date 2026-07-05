"""Injected on-disk layout for the tracker brick (HATS-864, ADR-0014 P0 #2).

Layout is integrator policy: the brick declares WHAT it needs, the integrator
says WHERE — via :func:`ai_hats.paths.tracker_paths`, the only sanctioned
integrator-side constructor (it wires the HATS-839 ``ensure_base`` guard).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class TrackerPaths:
    """Frozen layout contract consumed by :class:`ai_hats.state.TaskManager`."""

    tasks_dir: Path
    state_md_path: Path
    legacy_backlog_md: Path
    # None → brick mkdirs its injected dirs; the integrator passes the
    # validated project-root creator (paths.ensure_ai_hats_dir, HATS-839).
    ensure_base: Callable[[], Path] | None = None

    def ensure(self) -> None:
        """Run the injected base guard, or create the injected dirs bare."""
        if self.ensure_base is not None:
            self.ensure_base()
        else:
            self.tasks_dir.parent.mkdir(parents=True, exist_ok=True)


__all__ = ["TrackerPaths"]
