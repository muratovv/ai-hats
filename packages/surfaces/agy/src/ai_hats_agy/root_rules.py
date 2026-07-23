"""Hide the workspace root rule files agy auto-scans, and put them back.

agy merges a project's root ``GEMINI.md`` / ``AGENTS.md`` on top of the
``--add-dir`` rules, so a session role would silently inherit project-level
rules. They are renamed out of the way for the duration of a run.

Restoring cannot live only in the exit path: ``finally`` does not run on SIGKILL
or ``os.execv`` (HATS-1135), and the original would stay hidden forever. So the
backup name carries the owning pid, and entry reclaims what a dead session
abandoned — while leaving a live session's backup alone.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Sequence

BACKUP_INFIX = ".ai_hats_bak_"


def _backup_glob(target: Path) -> str:
    return f".{target.name}{BACKUP_INFIX}*"


def _owner_alive(backup: Path) -> bool:
    """Is the session that hid this file still running? Unknown owner reads as alive."""
    try:
        pid = int(backup.name.rsplit("_", 1)[-1])
    except ValueError:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError and friends: alive, just not ours
    return True


def reclaim_abandoned(targets: Sequence[Path]) -> None:
    """Restore what a killed session left hidden; drop stale copies."""
    for target in targets:
        orphans = [p for p in target.parent.glob(_backup_glob(target)) if not _owner_alive(p)]
        if not orphans:
            continue
        orphans.sort(key=lambda p: p.stat().st_mtime)
        newest = orphans.pop()
        if target.exists() or target.is_symlink():
            newest.unlink()  # the original is back; the hidden copy is stale
        else:
            newest.rename(target)
        for stale in orphans:
            stale.unlink()


@contextmanager
def hidden(project_dir: Path, filenames: Sequence[str]) -> Generator[None, None, None]:
    """Hide ``filenames`` under ``project_dir`` for the duration of the block."""
    targets = [project_dir / name for name in filenames]
    reclaim_abandoned(targets)

    moved: list[tuple[Path, Path]] = []
    try:
        for target in targets:
            if target.exists() or target.is_symlink():
                # The suffix names the OWNER, so a later session can tell an
                # abandoned backup from one a live session is holding.
                backup = target.with_name(f".{target.name}{BACKUP_INFIX}{os.getpid()}")
                target.rename(backup)
                moved.append((target, backup))
        yield
    finally:
        for target, backup in moved:
            if backup.exists() or backup.is_symlink():
                if target.exists() or target.is_symlink():
                    target.unlink()
                backup.rename(target)
