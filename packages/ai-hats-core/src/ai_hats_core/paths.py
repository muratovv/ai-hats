"""Filesystem path resolution shared across ai-hats packages.

Worktree-free primitives every package's standalone CLI can reuse without
depending on the integrator. The integrator layers its own richer resolvers on
top — e.g. the linked-worktree hop in ``ai_hats.cli._helpers._project_dir``,
which needs ai-hats-wt and so cannot live here.
"""

from __future__ import annotations

from pathlib import Path


def default_project_dir() -> Path:
    """Resolve the project root by walking up from CWD, worktree-free.

    Prefer the nearest ancestor (incl. CWD) holding ``.agent/``; else the nearest
    holding a ``.git`` dir or file; else CWD. This is the standalone default the
    tracker / observe CLIs use when no integrator override is injected — it does
    NOT hop from a linked-worktree gitlink to the main checkout (that hop needs
    ai-hats-wt and lives in the integrator's ``_project_dir``).
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
