"""Project-root resolution for the rack (HATS-1021, K2 of epic HATS-1014).

Pure walk-up resolver (HATS-197 heir) + the single validating entry point
(HATS-839 heir): resolution NEVER creates directories, and an unrecognized
root answers with a typed error instead of bootstrapping a phantom tracker.
Callers pass ``caller_cwd`` explicitly — no function here reads ``Path.cwd()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import fastyaml
from .errors import ForeignProjectPinError, RackError

CONFIG_NAME = "ai-hats.yaml"
#: schema defaults mirrored from the live project config (ai-hats.yaml).
DEFAULT_AI_HATS_DIR = ".agent/ai-hats"
DEFAULT_PREFIX = "HATS"
#: backlog layout under <ai_hats_dir> — same tree the production tracker uses,
#: so K6 compares both CLIs on one sandbox copy without relocation.
TASKS_SUBPATH = Path("tracker") / "backlog" / "tasks"

ENV_AI_HATS_DIR = "AI_HATS_DIR"
ENV_AI_HATS_PROJECT_DIR = "AI_HATS_PROJECT_DIR"


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


def _main_worktree_root(start: Path) -> Path | None:
    """Pure-fs gitlink hop (HATS-1038 C2): if an ancestor is a linked worktree
    (``.git`` is a *file* ``gitdir: <path>``), return the main checkout root; a
    ``.git`` *directory* means we're already in the main repo → None.

    No ``git`` subprocess — the rack forbids shelling out (import-hygiene pin);
    the git-worktree metadata (``gitdir`` + ``commondir``) is read directly.
    """
    for candidate in (start, *start.parents):
        git = candidate / ".git"
        if git.is_dir():
            return None
        if git.is_file():
            return _resolve_gitlink(git)
    return None


def _resolve_gitlink(git_file: Path) -> Path | None:
    """``<wt>/.git`` (``gitdir: <maindotgit>/worktrees/<name>``) → main root."""
    try:
        text = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text[len("gitdir:") :].strip())
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    try:
        common_rel = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    common = (gitdir / common_rel).resolve()
    # main worktree root = parent of the shared ``.git`` directory.
    return common.parent if common.name == ".git" else None


def find_project_root(start: Path) -> Path | None:
    """The project root for ``start``: nearest ``.agent/``/ai-hats.yaml ancestor,
    but a linked-worktree checkout resolves to its MAIN checkout first — a task
    worktree carries neither marker (or a stray copy of one), so it is never the
    real root (HATS-1038 C2).

    Pure walk-up: reads the filesystem, mutates nothing (HATS-197: an eager
    mkdir on a mis-resolved root is how stray trackers were born).
    """
    hop = _main_worktree_root(start)
    if hop is not None and ((hop / ".agent").is_dir() or (hop / CONFIG_NAME).is_file()):
        return hop
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
            raw = fastyaml.load(config_path.read_text(encoding="utf-8"))
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


def env_ai_hats_dir(environ: Mapping[str, str], project_dir: Path) -> Path | None:
    """Read ``AI_HATS_DIR`` from *environ*, respecting ``AI_HATS_PROJECT_DIR`` pin (# HATS-1471).

    Returns ``None`` if ``AI_HATS_DIR`` is unset or empty string.
    Raises :class:`ForeignProjectPinError` if ``AI_HATS_PROJECT_DIR`` is set and does
    not match *project_dir*.
    """
    raw = environ.get(ENV_AI_HATS_DIR)
    if not raw:
        return None
    pin_raw = environ.get(ENV_AI_HATS_PROJECT_DIR)
    if pin_raw:
        pin_path = Path(pin_raw).expanduser().resolve()
        if pin_path != project_dir.resolve():
            raise ForeignProjectPinError(pin=Path(pin_raw).expanduser(), project_dir=project_dir)
    return Path(raw).expanduser()


def resolve_root(
    caller_cwd: Path,
    tasks_dir_override: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RackRoot:
    """The single validating resolver every rack command goes through (# HATS-1471).

    Precedence order (top to bottom):
    1. ``tasks_dir_override`` (``--tasks-dir`` / ``RACK_TASKS_DIR``)
    2. ``AI_HATS_DIR`` env override (pair-guarded by ``AI_HATS_PROJECT_DIR``)
    3. ``ai-hats.yaml: ai_hats_dir`` (walk-up from ``caller_cwd``)
    4. ``DEFAULT_AI_HATS_DIR`` (``.agent/ai-hats``)

    Without an explicit override, ``AI_HATS_DIR`` env is consulted before walk-up;
    a start without project markers or env override raises :class:`NoProjectRootError`.
    Raises :class:`ForeignProjectPinError` if ``AI_HATS_PROJECT_DIR`` pin does not
    match the resolved project root.
    """
    if tasks_dir_override is not None:
        project_dir = find_project_root(caller_cwd)
        if project_dir is None:
            return RackRoot(project_dir=caller_cwd, tasks_dir=tasks_dir_override)
        base = load_root(project_dir)
        return RackRoot(
            project_dir=base.project_dir, tasks_dir=tasks_dir_override, prefix=base.prefix
        )

    project_dir = find_project_root(caller_cwd)

    if environ is not None:
        env_dir = env_ai_hats_dir(environ, project_dir or caller_cwd)
        if env_dir is not None:
            anchor = project_dir or caller_cwd
            base = load_root(anchor)
            return RackRoot(
                project_dir=base.project_dir,
                tasks_dir=env_dir / TASKS_SUBPATH,
                prefix=base.prefix,
            )

    if project_dir is None:
        raise NoProjectRootError(caller_cwd)

    return load_root(project_dir)
