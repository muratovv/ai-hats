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
from typing import Protocol

from .paths import session_cache_root, versions_root
from .version_lock import GC_LOCK_TIMEOUT, VersionLockError, versions_lock
from .version_recovery import (
    reclaim_legacy_venv,
    reclaim_orphan_versions,
    sweep_incomplete_versions,
)
from .version_refs import write_current_run_ref

logger = logging.getLogger(__name__)

SESSION_CACHE_TTL_HOURS = 24


def _sweep_orphan_session_caches(
    project_dir: Path, ttl_hours: int = SESSION_CACHE_TTL_HOURS
) -> None:
    """Remove session cache dirs older than ``ttl_hours`` (HATS-294).

    Idempotent. Called once per run at the ``create_session`` chokepoint. Cheap
    when the cache root is empty or recent. (Moved here from ``runtime`` in
    HATS-649 so it sits beside the other recovery passes; ``runtime`` re-exports
    it for backward compatibility.)
    """
    root = session_cache_root(project_dir)
    if not root.exists():
        return
    cutoff = time.time() - ttl_hours * 3600
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok session-cache (TTL sweep)
        except OSError:
            pass


class RecoveryProtocol(Protocol):
    """The collaborator contract ``SessionManager`` depends on."""

    def run(self) -> None: ...


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
                        logger.info(
                            "reclaimed incomplete version residue: %s", residue.name
                        )
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


class NoOpRecovery:
    """No-op recovery — for unit tests / contexts that must not touch the FS."""

    def run(self) -> None:
        return
