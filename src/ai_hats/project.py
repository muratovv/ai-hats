"""``Project`` — the one missing value: layout + config + resolved library layers.

SKETCH (HATS-1606): illustrates the contract; helper bodies elided.

No factory in this module ON PURPOSE: the value is resolved at the composition
root (``cli/_entry.py``), exactly once per process, and passed down. Deep code
declares ``project: Project`` and receives it — importing this module gives you
the type to ask for, never a way to re-derive one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from .config.project import ProjectConfig


@dataclass(frozen=True)
class Project:
    """Resolved once at the entry point; nothing below re-reads cwd, env, or yaml.

    Out-of-process consumers — hooks — cannot take the object: the executor
    that holds it marshals the anchors explicitly (each ``Surface.get_env``),
    and the hook-side entry point deserializes. A hook never computes the
    project from its own cwd.
    """

    layout: ProjectLayout
    config: ProjectConfig
    venv: Path  # env > config.venv_path > layout.default_venv — settled at build, not at access
    library_paths: tuple[Path, ...]  # builtin layers + config.library_paths, resolved
