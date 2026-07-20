"""wt-injection seam for the wt-optional backlog CLI (HATS-934).

The tracker CLI (`task`, `attach`) defaults to worktree-free constructors so it
runs with only ai-hats-core. The integrator overrides these module globals at
mount (`cli/__init__.py`) with the wt-wired `_helpers` versions, restoring
`ai-hats task`'s worktree UX. Reference the slots as ``_seam.<slot>`` (attribute
access at call time) so one integrator override reaches every importer.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core.paths import default_project_dir
from rich.console import Console

# The wt-free project-root resolver is shared via core (HATS-952) — the observe
# seam delegates to the same primitive; the integrator injects its wt-coupled one
# (``_helpers._project_dir``, which adds the linked-worktree hop that needs wt).
_default_project_dir = default_project_dir


def _default_task_manager(project_dir: Path | None = None):
    """Construct a wt-free ``TaskManager`` on a project-local ``.agent`` layout.

    Standalone default (``worktree_effects=None``, no ``ai-hats.yaml``); the
    integrator overrides this slot with its wt-wired, yaml-aware factory.
    """
    from ..layout import TrackerPaths
    from ..state import TaskManager

    pdir = project_dir or _default_project_dir()
    agent = pdir / ".agent"
    layout = TrackerPaths(
        tasks_dir=agent / "tasks",
        state_md_path=agent / "STATE.md",
        legacy_backlog_md=agent / "BACKLOG.md",
        ensure_base=None,
    )
    return TaskManager(pdir, layout=layout, worktree_effects=None)


def _default_guard_not_inside_linked_worktree() -> None:
    """No-op without ai-hats-wt: there are no linked worktrees to guard."""
    return None


def _default_hypotheses_dir(project_dir: Path) -> Path:
    """Standalone hypotheses dir: ``<project>/.agent/hypotheses`` (wt-free).

    The integrator overrides this with ``ai_hats.paths.hypotheses_dir``, which
    honours ``AI_HATS_DIR``/yaml precedence — a package hardcode would be wrong
    there, so the resolver is injected rather than derived here. Like the
    standalone task dir, this flat ``.agent/<kind>`` layout deliberately differs
    from the integrator's ``<ai_hats_dir>/tracker/…`` subtree.
    """
    return project_dir / ".agent" / "hypotheses"


def _default_hypotheses_flat_dir(project_dir: Path) -> Path:
    """Standalone legacy-flat hypotheses dir — same as the catalog when wt-free.

    The integrator overrides this with ``ai_hats.paths.hypotheses_flat_dir`` (the old
    ``tracker/hypotheses``); standalone has no migration split, so flat == catalog."""
    return _default_hypotheses_dir(project_dir)


def _default_proposals_dir(project_dir: Path) -> Path:
    """Standalone proposals dir: ``<project>/.agent/proposals`` (wt-free)."""
    return project_dir / ".agent" / "proposals"


# Injectable slots — the integrator overrides these at mount (cli/__init__.py).
_MANAGER_FACTORY = _default_task_manager
_PROJECT_DIR = _default_project_dir
_GUARD_LINKED_WT = _default_guard_not_inside_linked_worktree
_CONSOLE = Console()
# Integrator-only wt-state-dir resolver (``ai_hats.paths.worktrees_dir``); None
# standalone → the wt-present post-execute worktree display stays dark.
_WORKTREES_DIR = None
# hyp/prop path resolvers (HATS-935). Integrator overrides with
# ``ai_hats.paths.{hypotheses_dir,hypotheses_flat_dir,proposals_dir}`` (AI_HATS_DIR/
# yaml-aware). ``_HYPOTHESES_FLAT_DIR`` is the legacy flat fallback (HATS-1054).
_HYPOTHESES_DIR = _default_hypotheses_dir
_HYPOTHESES_FLAT_DIR = _default_hypotheses_flat_dir
_PROPOSALS_DIR = _default_proposals_dir
