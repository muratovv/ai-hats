"""Project-root resolution for the rack (HATS-1021, K2 of epic HATS-1014).

Pure walk-up resolver (HATS-197 heir) + the single validating entry point
(HATS-839 heir): resolution NEVER creates directories, and an unrecognized
root answers with a typed error instead of bootstrapping a phantom tracker.
Callers pass ``caller_cwd`` explicitly — no function here reads ``Path.cwd()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import RackError

CONFIG_NAME = "ai-hats.yaml"
#: schema defaults mirrored from the live project config (ai-hats.yaml).
DEFAULT_AI_HATS_DIR = ".agent/ai-hats"
DEFAULT_PREFIX = "HATS"
#: backlog layout under <ai_hats_dir> — same tree the production tracker uses,
#: so K6 compares both CLIs on one sandbox copy without relocation.
TASKS_SUBPATH = Path("tracker") / "backlog" / "tasks"


class NoProjectRootError(RackError):
    """No ancestor of the starting directory is an ai-hats project root."""

    def __init__(self, start: Path) -> None:
        self.start = start
        super().__init__(
            f"No project root found walking up from {start}: no ancestor holds "
            f"'.agent/' or '{CONFIG_NAME}'. Run inside an ai-hats project, or "
            "pass --tasks-dir / RACK_TASKS_DIR explicitly."
        )


@dataclass(frozen=True)
class RackRoot:
    """Resolved project anchor: where the backlog lives and how ids look."""

    project_dir: Path
    tasks_dir: Path
    prefix: str = DEFAULT_PREFIX


def find_project_root(start: Path) -> Path | None:
    """Nearest ancestor (including ``start``) holding ``.agent/`` or ai-hats.yaml.

    Pure walk-up: reads the filesystem, mutates nothing (HATS-197: an eager
    mkdir on a mis-resolved root is how stray trackers were born).
    """
    for candidate in (start, *start.parents):
        if (candidate / ".agent").is_dir() or (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def load_root(project_dir: Path) -> RackRoot:
    """Read the root's ai-hats.yaml (if any) into a :class:`RackRoot`.

    Only ``ai_hats_dir`` and ``task_prefix`` are consumed; both default to the
    live schema values. A malformed config falls back to defaults rather than
    failing a read-only verb.
    """
    ai_hats_dir = DEFAULT_AI_HATS_DIR
    prefix = DEFAULT_PREFIX
    config_path = project_dir / CONFIG_NAME
    if config_path.is_file():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raw = None
        if isinstance(raw, dict):
            ai_hats_dir = str(raw.get("ai_hats_dir") or ai_hats_dir)
            prefix = str(raw.get("task_prefix") or prefix)
    return RackRoot(
        project_dir=project_dir,
        tasks_dir=project_dir / ai_hats_dir / TASKS_SUBPATH,
        prefix=prefix,
    )


def resolve_root(caller_cwd: Path, tasks_dir_override: Path | None = None) -> RackRoot:
    """The single validating resolver every rack command goes through.

    An explicit override (``--tasks-dir`` / ``RACK_TASKS_DIR``) is honored
    as-is — explicit intent, K1 contract. Without it the root is walked up
    from ``caller_cwd``; a start with no project marker raises the typed
    :class:`NoProjectRootError` with zero side effects — directories are only
    ever created later, by kernel write ops under a validated root (HATS-839).
    """
    if tasks_dir_override is not None:
        return RackRoot(project_dir=caller_cwd, tasks_dir=tasks_dir_override)
    project_dir = find_project_root(caller_cwd)
    if project_dir is None:
        raise NoProjectRootError(caller_cwd)
    return load_root(project_dir)
