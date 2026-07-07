"""wt-injection seam for the wt-optional backlog CLI (HATS-934).

The tracker CLI (`task`, `attach`) defaults to worktree-free constructors so it
runs with only ai-hats-core. The integrator overrides these module globals at
mount (`cli/__init__.py`) with the wt-wired `_helpers` versions, restoring
`ai-hats task`'s worktree UX. Reference the slots as ``_seam.<slot>`` (attribute
access at call time) so one integrator override reaches every importer.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console


def _default_project_dir() -> Path:
    """Resolve the project root by walking up from CWD, wt-free.

    Mirrors the integrator's ``_helpers._project_dir`` minus the linked-worktree
    hop: on a ``.git`` *file* (gitlink) it returns the holding dir instead of
    hopping to the main checkout (that hop needs ai-hats-wt).
    """
    cwd = Path.cwd()
    candidates = [cwd, *cwd.parents]
    for d in candidates:
        if (d / ".agent").is_dir():
            return d
    for d in candidates:
        git = d / ".git"
        if git.is_dir() or git.is_file():
            return d
    return cwd


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
# ``ai_hats.paths.{hypotheses_dir,proposals_dir}`` (AI_HATS_DIR/yaml-aware).
_HYPOTHESES_DIR = _default_hypotheses_dir
_PROPOSALS_DIR = _default_proposals_dir
