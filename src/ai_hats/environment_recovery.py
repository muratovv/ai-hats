"""Convergent environment recovery at the ``create_session`` chokepoint (HATS-649 / R2).

Every run — HITL (``WrapRunner``) and Automate (``SubAgentRunner``) alike —
traverses ``SessionManager.create_session``. R2 makes that the universal seam for
the off-exit-path recovery passes, closing the gap where they ran only on the
WrapRunner path:

  1. write this run's liveness ref (so a concurrent reclaim never deletes the
     version *we* are pinned to);
  2. sweep stale session-cache dirs (HATS-294);
  3. sweep incomplete versioned-install residue (HATS-648 / R1);
  4. reclaim orphaned complete versions with no live ref (HATS-649 / R2);
  5. reclaim the legacy pre-versioning ``.venv`` once we run from a complete
     versioned venv (HATS-653 / Phase B).

Steps 3–4 (the version GC) run under the crash-safe ``versions/.gc.lock``
(HATS-650 / R3), serialized against a concurrent ``self update`` and peer GC
passes; the lock acquire is opportunistic here (skipped on contention) so it
never blocks or breaks ``create_session``. Steps 1, 2 and 5 stay outside it.

Recovery is injected into ``SessionManager`` as a mockable collaborator
(:class:`EnvironmentRecovery` by default, :class:`NoOpRecovery` for unit tests
that must not touch the filesystem) — per the supervisor's DI decision.

Leaf module by design: it imports only ``paths`` / ``version_recovery`` /
``version_refs`` (all leaves), so ``observe`` and ``runtime`` can both depend on
it without an import cycle.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

# HATS-948: RecoveryProtocol + NoOpRecovery promoted to core; re-exported here so
# consumers (observe, tests) import unchanged. EnvironmentRecovery stays integrator.
from ai_hats_core.recovery import NoOpRecovery, RecoveryProtocol  # noqa: F401

from .paths import ai_hats_dir, cache_home, cache_root, session_cache_root, versions_root
from .version_lock import GC_LOCK_TIMEOUT, VersionLockError, versions_lock
from .version_recovery import (
    reclaim_legacy_venv,
    reclaim_orphan_versions,
    sweep_incomplete_versions,
)
from .version_refs import write_current_run_ref

logger = logging.getLogger(__name__)

SESSION_CACHE_TTL_HOURS = 24
PROJECT_KEY_TTL_DAYS = 9


def _sweep_orphan_session_caches(
    project_dir: Path, ttl_hours: int = SESSION_CACHE_TTL_HOURS
) -> None:
    """Remove session cache dirs older than ``ttl_hours`` (HATS-294).

    Idempotent. Called once per run at the ``create_session`` chokepoint. Cheap
    when the cache root is empty or recent. (Moved here from ``runtime`` in
    HATS-649 so it sits beside the other recovery passes; ``runtime`` re-exports
    it for backward compatibility.)
    """
    cutoff = time.time() - ttl_hours * 3600
    _expire_session_dirs(session_cache_root(project_dir), cutoff)
    _drain_workspace_cache(ai_hats_dir(project_dir) / ".cache", cutoff)


def _expire_session_dirs(root: Path, cutoff: float) -> None:
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok session-cache (TTL)
        except OSError:
            pass


def _drain_workspace_cache(legacy: Path, cutoff: float) -> None:
    """Delete the pre-HATS-1398 in-tree cache; nothing writes there any more.

    The cache is regenerable, so it is dropped rather than migrated. Only session
    dirs wait for the TTL — one may belong to a session that started before the
    move and is still reading it.
    """
    if not legacy.is_dir():
        return
    for entry in legacy.iterdir():
        if entry.name == "sessions":
            _expire_session_dirs(entry, cutoff)
            _rmdir_quiet(entry)
            continue
        logger.info("dropping stale in-tree cache %s (HATS-1398)", entry)
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok regenerable cache
            else:
                entry.unlink()  # safe-delete: ok regenerable cache
        except OSError:
            pass
    _rmdir_quiet(legacy)


def _key_last_touched(key_dir: Path) -> float:
    """Newest mtime across ``key_dir`` and its immediate children.

    A directory's own mtime moves only when a DIRECT child is added or removed,
    so a live project whose writes land deeper (``sessions/<sid>/...``) reads as
    untouched. Both a session start and a probe fetch do touch a direct child.
    """
    newest = key_dir.stat().st_mtime
    for child in key_dir.iterdir():
        newest = max(newest, child.stat().st_mtime)
    return newest


def _sweep_orphan_project_keys(project_dir: Path, ttl_days: int = PROJECT_KEY_TTL_DAYS) -> None:
    """Remove sibling project-key dirs untouched for ``ttl_days`` (HATS-1473).

    ``_sweep_orphan_session_caches`` only ever walks into the CURRENT project's
    key, so a key left by a renamed, deleted or one-off project was unreachable
    for every mechanism and accumulated forever — 3939 keys / 16.9 GB measured.
    The key is a one-way hash of the project path, so liveness cannot be resolved
    back to a directory; age is the only available signal, and the cache is
    regenerable, so a wrong guess costs a rebuild.
    """
    own_key = cache_root(project_dir).name
    cutoff = time.time() - ttl_days * 86400
    try:
        entries = list(cache_home().iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == own_key or not entry.is_dir():
            continue
        try:
            if _key_last_touched(entry) < cutoff:
                shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok regenerable cache
                logger.info("reclaimed orphaned project cache key: %s", entry.name)
        except OSError as exc:
            logger.warning("project-key sweep skipped %s: %s", entry.name, exc)


def _rmdir_quiet(path: Path) -> None:
    """Drop ``path`` only when empty — ``rmdir`` refuses a non-empty dir by contract."""
    try:
        path.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        pass


class EnvironmentRecovery:
    """Real recovery: ref-write first (protect our own pin), then sweeps + reclaim."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def run(self) -> None:
        # Order matters: write THIS run's ref before any reclaim can observe the
        # version we are pinned to as orphaned. A run started before a `self
        # update` flipped `current` is pinned to a now-non-current sha; its ref
        # is what protects that dir from a concurrent reclaim. The ref write and
        # the session-cache sweep stay OUTSIDE the version lock: the ref must
        # never be skipped (it declares our pin), and the cache sweep mutates a
        # different tree (sessions/, not versions/).
        write_current_run_ref(self.project_dir)
        _sweep_orphan_session_caches(self.project_dir)
        _sweep_orphan_project_keys(self.project_dir)

        # The version GC mutates versions/ — serialize it against a concurrent
        # `self update` (acquire) or a peer GC pass under the crash-safe lock
        # (HATS-650 / R3). Skip when versions/ does not exist (legacy .venv /
        # fresh project): nothing to reclaim, and we must not create versions/
        # just to lock it. Opportunistic: on contention the lock is already held
        # by an installer or a peer GC, so we skip and let this session start —
        # the next invocation converges. The GC must NEVER block or break
        # create_session: a lock timeout is swallowed (INFO), and an I/O error
        # mid-sweep (a vanishing dir, permissions, a full disk) is swallowed too
        # (WARNING — no-silent-caps) rather than propagated up through
        # create_session; the next invocation retries.
        if versions_root(self.project_dir).exists():
            try:
                with versions_lock(self.project_dir, timeout=GC_LOCK_TIMEOUT):
                    for residue in sweep_incomplete_versions(self.project_dir):
                        logger.info("reclaimed incomplete version residue: %s", residue.name)
                    for orphan in reclaim_orphan_versions(self.project_dir):
                        logger.info("reclaimed orphaned version: %s", orphan.name)
            except VersionLockError:
                logger.info(
                    "version GC skipped: lock held by another ai-hats process "
                    "(install or concurrent GC); next invocation will retry"
                )
            except OSError as exc:
                logger.warning("version GC skipped on I/O error: %s", exc)

        # HATS-653 (Phase B): once we run from a complete versioned venv, the
        # orphaned pre-versioning legacy .venv is dead weight — reclaim it.
        # OUTSIDE the version lock: .venv lives outside versions/, the reclaim is
        # idempotent, and its current_run_sha guard makes it a no-op on a
        # legacy/override/editable run, so it is safe at this universal seam.
        reclaimed_venv = reclaim_legacy_venv(self.project_dir)
        if reclaimed_venv is not None:
            logger.info("reclaimed legacy .venv: %s", reclaimed_venv)
