"""Pre-bump backup snapshot (HATS-549).

Before any install-time work touches the project tree, snapshot the
ai-hats-managed surface to ``/tmp/`` as a tarball — the always-on recovery path
if a later step (migration registry, healer, layout move) misbehaves::

    tar -xzf <snapshot>.tar.gz -C <project_dir>

The captured paths are ``BACKUP_SCOPE_PATHS`` (the ``.agent/`` tree, the
``.claude`` settings, ``ai-hats.yaml``, ``CLAUDE.md`` / ``AGY.md``,
``.githooks``, ``.gitignore``); everything else under ``project_dir`` is the
user's project and is skipped. Env: ``AI_HATS_BUMP_BACKUP_DIR=<path>`` overrides
the base dir; ``=-`` hard-disables (one stderr WARN/call) for CI / ephemeral
envs. A snapshot write failure (ENOSPC / read-only fs) raises ``BackupError`` and
callers abort rather than silently lose recovery. Retention: last
``MAX_RETENTION`` per project slug, older unlinked best-effort. See
``tracker/backlog/tasks/HATS-549/plan.md`` for full design.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    CLAUDE_MD_FILENAME,
    CLAUDE_SETTINGS_JSON_REL,
    CLAUDE_SETTINGS_LOCAL_JSON_REL,
    GEMINI_MD_FILENAME,
    PROJECT_CONFIG,
)

__all__ = [
    "snapshot_pre_bump",
    "latest_snapshot",
    "BackupError",
    "ENV_BACKUP_DIR",
    "HARD_DISABLE_SENTINEL",
    "MAX_RETENTION",
    "BACKUP_SCOPE_PATHS",
    "EXCLUDED_BASENAMES",
]


ENV_BACKUP_DIR = "AI_HATS_BUMP_BACKUP_DIR"
HARD_DISABLE_SENTINEL = "-"
NAMESPACE = "ai-hats"
SUBDIR = "bump-backups"
MAX_RETENTION = 10

# Paths under ``project_dir`` that enter the tarball. Each entry is a
# project-relative path; missing entries are silently skipped (e.g.
# greenfield projects won't have ``.agent/`` yet, ``AGY.md`` only
# exists on the agy provider, etc.).
BACKUP_SCOPE_PATHS: tuple[str, ...] = (
    ".agent",
    CLAUDE_SETTINGS_JSON_REL,
    CLAUDE_SETTINGS_LOCAL_JSON_REL,
    PROJECT_CONFIG,
    CLAUDE_MD_FILENAME,
    GEMINI_MD_FILENAME,
    ".githooks",
    ".gitignore",
)

# Basenames pruned at ANY depth inside the scope: regenerable derived state
# (venv, bytecode, caches, node_modules) whose inclusion would inflate the
# tarball ~100x (the venv alone is ~85 MB) for zero recovery value — rebuilt by
# `pip install -e` / bytecode recompile.
EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "__pycache__",
        ".cache",
        "node_modules",
    }
)


def _should_exclude(arcname: str) -> bool:
    """True when any path segment of ``arcname`` is in EXCLUDED_BASENAMES
    or matches a bytecode pattern (``*.pyc``).

    Uses POSIX-style ``/`` segmentation — tarfile always normalises to
    forward slashes regardless of host OS.
    """
    segments = arcname.split("/")
    if any(s in EXCLUDED_BASENAMES for s in segments):
        return True
    if arcname.endswith(".pyc"):
        return True
    return False


class BackupError(OSError):
    """Raised when the pre-bump snapshot cannot be written.

    Callers MUST treat this as fatal — proceeding with migration
    without a recoverable snapshot violates the safety contract.
    """


def _resolve_base() -> tuple[Path | None, bool]:
    """Return ``(base_dir, hard_disabled)``.

    ``base_dir`` is ``None`` when ``hard_disabled=True``.
    """
    env = os.environ.get(ENV_BACKUP_DIR, "").strip()
    if env == HARD_DISABLE_SENTINEL:
        return None, True
    if env:
        return Path(env).expanduser(), False
    return Path(tempfile.gettempdir()) / NAMESPACE / SUBDIR, False


def _project_slug(project_dir: Path) -> str:
    """8-char sha256 hex of the resolved absolute project path.

    Deterministic per project, isolates retention sweeps to one project
    at a time (so a backup from project A never gets unlinked when
    project B does its tenth bump).
    """
    abs_str = str(project_dir.resolve())
    return hashlib.sha256(abs_str.encode()).hexdigest()[:8]


def _sweep_retention(base: Path, slug: str, keep: int = MAX_RETENTION) -> None:
    """Delete old snapshots for ``slug``, keeping only the newest ``keep``.

    Best-effort: any OSError is swallowed (next call will retry). The
    point of retention is to bound disk usage; missing it once is not
    a safety regression.
    """
    if not base.is_dir():
        return
    # Snapshot filenames are ``<utc_ts>-<slug>-<label>.tar.gz`` — the
    # timestamp prefix makes lexicographic order match chronological
    # order, so sorting by name is sufficient.
    candidates = sorted(
        (p for p in base.iterdir() if p.is_file() and f"-{slug}-" in p.name),
    )
    excess = len(candidates) - keep
    if excess <= 0:
        return
    for old in candidates[:excess]:
        try:
            old.unlink()  # safe-delete: ok retention prune of own migration backups
        except OSError:
            # Concurrent unlink, permission flap, etc. The next sweep
            # will retry. Not a safety issue — retention is purely
            # for disk hygiene.
            continue


def _iter_scope(project_dir: Path) -> list[Path]:
    """Return scope paths that actually exist on disk, in declaration order.

    Missing paths are silently skipped — greenfield projects, providers
    that don't use ``AGY.md``, etc.
    """
    out: list[Path] = []
    for rel in BACKUP_SCOPE_PATHS:
        p = project_dir / rel
        if p.exists():
            out.append(p)
    return out


def snapshot_pre_bump(
    project_dir: Path,
    label: str = "bump",
) -> Path | None:
    """Snapshot the ai-hats-managed surface of ``project_dir`` to ``/tmp/``.

    Returns the absolute path to the resulting ``.tar.gz`` file, or
    ``None`` when the hard-disable sentinel is set
    (``AI_HATS_BUMP_BACKUP_DIR=-``). The path is also printed to stderr
    with the standard ``[ai-hats]`` banner so the user sees the
    recovery handle BEFORE any destructive work runs.

    Parameters:
        project_dir: Absolute or relative project root. ``resolve()``-ed
            before use so the slug is stable regardless of how the
            caller phrased the path.
        label: Short tag mixed into the filename (``bump`` / ``init``).
            Filename-safe characters only; not validated — caller
            controls it.

    Raises:
        BackupError: when ``/tmp/`` (or the env-override) is not
            writable, or the tarball write fails. Callers MUST treat
            this as fatal.
    """
    base, hard_disabled = _resolve_base()
    if hard_disabled:
        print(
            f"[ai-hats] WARN: {ENV_BACKUP_DIR}={HARD_DISABLE_SENTINEL} — "
            "pre-bump backup DISABLED, no recovery snapshot will be written",
            file=sys.stderr,
        )
        return None

    assert base is not None  # narrow for mypy

    project_dir = project_dir.resolve()
    slug = _project_slug(project_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}-{slug}-{label}.tar.gz"

    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EACCES, errno.EROFS):
            raise BackupError(
                f"Cannot create backup dir {base}: {e.strerror}. "
                f"Set {ENV_BACKUP_DIR} to a writable path or use "
                f"{ENV_BACKUP_DIR}={HARD_DISABLE_SENTINEL} to disable "
                "(loses recovery capability)."
            ) from e
        raise

    target = base / filename
    scope = _iter_scope(project_dir)

    # Empty scope (true greenfield: no .agent/, no settings.json, no
    # ai-hats.yaml) — write a marker tarball anyway so the caller has a
    # consistent return type and the user has proof a bump ran. The
    # tarball will be ~empty but valid.
    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        # Drop symlinks (HATS-549 review S.1): preserves backup
        # restorability — a symlink pointing outside project_dir would
        # be archived as-is and silently violate the safety contract
        # on extract. On macOS APFS a symlink to an absolute path can
        # also surface OSError(EINVAL) on tarball write; dropping
        # them avoids the failure mode entirely. Symlinks are rare
        # under .agent/ and the user can recreate them post-restore
        # if they relied on one.
        if info.issym() or info.islnk():
            return None
        if _should_exclude(info.name):
            return None
        return info

    try:
        with tarfile.open(target, mode="w:gz") as tar:
            for entry in scope:
                arcname = str(entry.relative_to(project_dir))
                tar.add(
                    str(entry),
                    arcname=arcname,
                    recursive=True,
                    filter=_filter,
                )
    except OSError as e:
        # Clean up partial file before re-raising.
        try:
            target.unlink()  # safe-delete: ok cleanup of partial file on write failure
        except OSError:
            pass
        if e.errno in (errno.ENOSPC, errno.EACCES, errno.EROFS):
            raise BackupError(
                f"Cannot write backup tarball {target}: {e.strerror}. "
                f"Set {ENV_BACKUP_DIR} to a writable path or use "
                f"{ENV_BACKUP_DIR}={HARD_DISABLE_SENTINEL} to disable "
                "(loses recovery capability)."
            ) from e
        raise

    # Sweep AFTER writing — final on-disk count is MAX_RETENTION
    # regardless of how many stale entries we started with. Sweep-after
    # is the simpler invariant ("at end of snapshot_pre_bump, at most N
    # exist") and the disk-full case is already covered by the
    # BackupError above on the write itself.
    _sweep_retention(base, slug)

    print(
        f"[ai-hats] migration backup → {target}\n"
        f"          Recovery: tar -xzf {target} -C {project_dir}",
        file=sys.stderr,
    )
    return target


def latest_snapshot(project_dir: Path) -> Path | None:
    """Newest snapshot tarball for ``project_dir``, or ``None`` when there is none.

    Read-only counterpart to :func:`snapshot_pre_bump`, for callers that need the
    recovery handle without writing one (HATS-595 DATA remediation).
    """
    base, hard_disabled = _resolve_base()
    if hard_disabled or base is None or not base.is_dir():
        return None
    slug = _project_slug(project_dir.resolve())
    # Names are ``<utc_ts>-<slug>-<label>.tar.gz`` — lexicographic max is the
    # chronological newest (same ordering _sweep_retention relies on).
    return max(
        (p for p in base.iterdir() if p.is_file() and f"-{slug}-" in p.name),
        default=None,
    )
