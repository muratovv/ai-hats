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
