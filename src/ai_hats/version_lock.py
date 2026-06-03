"""Crash-safe advisory lock for the versioned-install critical sections (HATS-650 / R3).

R2 (HATS-649) gave ``versions/`` a GC pass (reclaim orphaned versions + sweep
incomplete residue) and ``self update`` an acquire pass (install + ``.complete``
+ flip ``current``). Both mutate the same ``versions/`` tree, and concurrent
``ai-hats`` processes are the norm — a sub-agent fan-out spawns N children, each
running the GC at the ``create_session`` chokepoint over the **same** tree. The
corrupting interleaving R3 closes: an installer writes ``.complete`` then flips
``current``; in that window the target dir is complete, non-``current`` and has
no live ref (the installer runs from the *old* sha), so a concurrent GC reclaims
it and the flip lands on a deleted dir → the tool bricks.

This module serializes those sections with a single ``versions/.gc.lock``.

**Why a library, not a hand-rolled lockfile** — this is a ~15-line wrapper over
``filelock`` (already a dependency), *not* a lock implementation. ``filelock``
uses ``fcntl`` advisory locks on POSIX, so the **kernel auto-releases the lock
when the holder process dies** (all its fds close). That is exactly the R3
property: a ``kill -9`` while the lock is held never wedges future cleanup — the
forbidden create-lockfile/delete-on-exit scheme would leak a stale file the next
process refuses to pass. The lock file itself is harmless to leave on disk; it
carries no state, only the kernel-held lock does. Mirrors ``worktree._acquire``
(worktree.py) verbatim — same library, same pattern.

The wrapper only adds what ``filelock`` does not: a single source of truth for
the lock path, the parent ``mkdir`` (``filelock`` does not create directories),
and the ``filelock.Timeout → VersionLockError`` translation.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import filelock

from .paths import versions_root

logger = logging.getLogger(__name__)

# The acquire site (``self update``) holds the lock across venv-create + pip +
# verify, which can run tens of seconds on a cold cache; a second concurrent
# update must outlast that. Generous, matching the e2e ``self update`` envelope.
INSTALL_LOCK_TIMEOUT = 300.0

# The GC site sits on the hot ``create_session`` path. It is opportunistic: on
# contention it skips (the holder is already cleaning/installing) rather than
# block a session start behind a slow install. A short wait absorbs brief
# GC-vs-GC overlap; a held install times out fast and is swallowed by the
# caller. (Deferred optimization, HATS-650 Out-of-scope: drop to 0 for an
# immediate try-skip.)
GC_LOCK_TIMEOUT = 2.0


def gc_lock_path(project_dir: Path) -> Path:
    """Single source of truth for the version GC/acquire lock: ``versions/.gc.lock``.

    Pure path helper — no ``mkdir`` (mirrors :func:`ai_hats.paths.versions_root`).
    Exposed so call sites and tests share one definition rather than a magic
    string.
    """
    return versions_root(project_dir) / ".gc.lock"


class VersionLockError(Exception):
    """Raised when acquiring the version GC/acquire lock times out (HATS-650)."""


@contextmanager
def versions_lock(project_dir: Path, *, timeout: float) -> Iterator[None]:
    """Hold the crash-safe ``versions/.gc.lock`` for a versioned-install section.

    Serializes the acquire (install + flip) and GC (reclaim + sweep) critical
    sections against concurrent ``ai-hats`` processes. The lock is an ``fcntl``
    advisory lock via :mod:`filelock`; the kernel releases it on process death,
    so a hard kill while held never wedges future cleanup.

    Raises :class:`VersionLockError` on timeout — the caller decides whether to
    propagate (the explicit ``self update`` acquire) or swallow and skip (the
    opportunistic hot-path GC).
    """
    lock_path = gc_lock_path(project_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    try:
        with lock:
            yield
    except filelock.Timeout as exc:
        raise VersionLockError(
            f"version GC/acquire lock held by another process for "
            f">{timeout:.0f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a concurrent 'ai-hats self update' or a stuck ai-hats "
            f"process — check: ps aux | grep ai-hats\n"
            f"  If safe, remove the lock file and retry."
        ) from exc
