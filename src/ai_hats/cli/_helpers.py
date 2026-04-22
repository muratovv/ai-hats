"""Shared helpers used by ≥2 CLI modules."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


def _project_dir() -> Path:
    return Path.cwd()


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
