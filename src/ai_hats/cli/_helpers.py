"""Shared helpers used by ≥2 CLI modules."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


def _project_dir() -> Path:
    """Resolve the project root by walking up from CWD.

    Order of preference:
      1. Nearest ancestor (incl. CWD itself) that contains `.agent/` —
         that ancestor IS the project root for this backlog.
      2. Nearest ancestor that contains `.git` (file or dir) — standard
         git-root semantics, used when the project hasn't been onboarded
         to ai-hats yet but the user is initializing it.
      3. Fallback: CWD (projects without VCS or pre-init scenarios).

    `.agent/` takes precedence over `.git/` so linked git worktrees that
    don't carry their own `.agent/` resolve up to the main project root,
    matching the user expectation that the backlog lives in one place per
    repo.
    """
    cwd = Path.cwd()
    candidates = [cwd, *cwd.parents]

    for d in candidates:
        if (d / ".agent").is_dir():
            return d

    for d in candidates:
        if (d / ".git").exists():
            return d

    return cwd


def _assembler(project_dir: Path | None = None):
    from ..assembler import Assembler

    return Assembler(project_dir or _project_dir())


def _task_manager(project_dir: Path | None = None):
    """Construct a TaskManager with the project's configured task-id prefix.

    Falls back to auto-detection (and persists the result) when the project
    has existing task folders but no `task_prefix` in ai-hats.yaml — keeps
    legacy repos on their historical prefix without manual migration.
    """
    from ..models import ProjectConfig
    from ..state import TaskManager

    pdir = project_dir or _project_dir()
    config_path = pdir / "ai-hats.yaml"
    prefix = ProjectConfig.resolve_task_prefix(pdir, config_path)
    return TaskManager(pdir, prefix=prefix)
