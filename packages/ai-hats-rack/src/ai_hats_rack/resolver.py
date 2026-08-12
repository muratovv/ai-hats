"""Project-root resolution for the rack (HATS-1021, K2 of epic HATS-1014).

Pure walk-up resolver (HATS-197 heir) + the single validating entry point
(HATS-839 heir): resolution NEVER creates directories, and an unrecognized
root answers with a typed error instead of bootstrapping a phantom tracker.
Callers pass ``caller_cwd`` explicitly — no function here reads ``Path.cwd()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    """Where the operator stands, where the backlog lives, and how ids look.

    ``project_dir`` is the ANCHOR — the caller's project (gate subprocess cwd,
    worktrees, the linked-worktree hop of HATS-1038 C2). ``backlog_owner`` is
    the project that OWNS ``tasks_dir`` — composition, prefix, STATE.md — and is
    ``None`` when no marker stands above the backlog. An explicit ``--tasks-dir``
    moves the backlog without moving the operator, so the two diverge and one
    field cannot serve both (HATS-1573). No default: a forgotten owner would
    compose nothing and drop a gate in silence.
    """  # comment-length: allow — the split between the two roles IS the contract

    project_dir: Path
    tasks_dir: Path
    backlog_owner: Path | None = field(kw_only=True)
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
    return find_marker_root(start)


def find_marker_root(start: Path) -> Path | None:
    """Nearest ancestor holding ``.agent/`` or ai-hats.yaml — markers only.

    No gitlink hop, deliberately: the hop is cwd semantics (HATS-1038 C2), and a
    backlog path is not a cwd. Applied to a sandbox under a linked worktree it
    would answer with the enclosing checkout — the HATS-1573 defect itself.
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
            raw = fastyaml.load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raw = None
        if isinstance(raw, dict):
            ai_hats_dir = str(raw.get("ai_hats_dir") or ai_hats_dir)
            prefix = str(raw.get("task_prefix") or prefix)
    return RackRoot(
        project_dir=project_dir,
        tasks_dir=project_dir / ai_hats_dir / TASKS_SUBPATH,
        backlog_owner=project_dir,  # derived FROM project_dir — they agree by construction
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
    ai_hats_dir = Path(raw).expanduser()
    pin_raw = environ.get(ENV_AI_HATS_PROJECT_DIR)
    if pin_raw:
        pin_path = Path(pin_raw).expanduser().resolve()
        if pin_path != project_dir.resolve():
            raise ForeignProjectPinError(
                pin=Path(pin_raw).expanduser(), project_dir=project_dir, ai_hats_dir=ai_hats_dir
            )
    return ai_hats_dir


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

    Every branch also answers ``backlog_owner`` — the project owning the resolved
    backlog, walked up from ``tasks_dir`` without the gitlink hop (HATS-1573).
    """  # comment-length: allow — the precedence order IS the contract
    if tasks_dir_override is not None:
        # A relative override is the CALLER's: left alone it made the walk-up
        # answer '.', which then travelled as a gate's AI_HATS_PROJECT_DIR.
        backlog = caller_cwd / tasks_dir_override.expanduser()
        owner = find_marker_root(backlog)
        return RackRoot(
            project_dir=find_project_root(caller_cwd) or caller_cwd,
            tasks_dir=backlog,
            backlog_owner=owner,
            # Ids name the backlog's project, never wherever the operator stands.
            prefix=load_root(owner).prefix if owner is not None else DEFAULT_PREFIX,
        )

    project_dir = find_project_root(caller_cwd)

    if environ is not None:
        env_dir = env_ai_hats_dir(environ, project_dir or caller_cwd)
        if env_dir is not None:
            anchor = project_dir or caller_cwd
            base = load_root(anchor)
            env_tasks_dir = env_dir / TASKS_SUBPATH
            owner = find_marker_root(caller_cwd / env_tasks_dir)
            return RackRoot(
                project_dir=base.project_dir,
                tasks_dir=env_tasks_dir,
                backlog_owner=owner,
                # Same rule as the override road above: a leaked AI_HATS_DIR must
                # not stamp this caller's prefix onto another project's backlog.
                prefix=load_root(owner).prefix if owner is not None else DEFAULT_PREFIX,
            )

    if project_dir is None:
        raise NoProjectRootError(caller_cwd)

    return load_root(project_dir)
