"""Convergent environment recovery at the ``create_session`` chokepoint (HATS-649 / R2).

Every run — HITL (``WrapRunner``) and Automate (``SubAgentRunner``) alike —
traverses ``SessionManager.create_session``. R2 makes that the universal seam for
the off-exit-path recovery passes, closing the gap where they ran only on the
WrapRunner path:

  1. write this run's liveness ref (so a concurrent reclaim never deletes the
     version *we* are pinned to);
  2. reap session-cache dirs whose owner is gone, and expire aged bulk run
     artifacts (HATS-294 / HATS-1339);
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
``version_refs`` / ``runs_retention`` / ``session_liveness`` (all leaves), so
``observe`` and ``runtime`` can both depend on it without an import cycle.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

# RecoveryProtocol + NoOpRecovery promoted to core; re-exported here so
# consumers (observe, tests) import unchanged. EnvironmentRecovery stays integrator.
from ai_hats_core.recovery import NoOpRecovery, RecoveryProtocol  # noqa: F401

from .paths import ai_hats_dir, cache_home, cache_root, session_cache_root, versions_root
from .runs_retention import sweep_runs
from .session_liveness import LazyLiveness as _LazyLiveness
from .session_liveness import session_owners
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
    project_dir: Path,
    ttl_hours: int = SESSION_CACHE_TTL_HOURS,
    *,
    liveness: _LazyLiveness | None = None,
) -> None:
    """Reap session cache dirs whose owner is gone (HATS-294 / HATS-1339).

    Idempotent. Called once per run at the ``create_session`` chokepoint. Cheap
    when the cache root is empty or every owner is alive. (Moved here from
    ``runtime`` in HATS-649 so it sits beside the other recovery passes;
    ``runtime`` re-exports it for backward compatibility.)
    """
    cutoff = time.time() - ttl_hours * 3600
    liveness = liveness or _LazyLiveness()
    _expire_session_dirs(session_cache_root(project_dir), cutoff, liveness)
    _drain_workspace_cache(ai_hats_dir(project_dir) / ".cache", cutoff, liveness)


def _expire_session_dirs(root: Path, cutoff: float, liveness: _LazyLiveness) -> None:
    """Drop each session dir whose owner is certainly dead; age only decides the
    ownerless ones.

    Age alone deleted 12 LIVE maintainer sessions' skills and ``hooks.json``
    mid-flight (plan Q1): a dir's mtime is stamped at creation and never
    refreshed, so a session merely crossing the TTL became a deletion candidate
    while running. Conversely a crashed session is reapable the moment its pid
    is gone, which is why the reclaim no longer waits out the TTL.
    """
    if not root.is_dir():
        return
    try:
        candidates = sorted(entry for entry in root.iterdir() if entry.is_dir())
    except OSError as exc:
        logger.warning("session-cache sweep skipped, %s unreadable: %s", root, exc)
        return
    for entry in candidates:
        reason = _reap_reason(entry, cutoff, liveness)
        if reason is None:
            continue
        shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok session-cache (dead owner/TTL)
        logger.warning("reclaimed session cache %s: %s", entry.name, reason)


def _reap_reason(entry: Path, cutoff: float, liveness: _LazyLiveness) -> str | None:
    """Why ``entry`` may be dropped, or ``None`` to keep it.

    Every process the anchor names must be gone, not just the wrapper: the
    wrapper owns the dir but the surface CLI is what READS it, and on the
    sub-agent path (pipes, no tty) the surface outlives a SIGKILLed wrapper
    indefinitely — measured at 45s and reparented to init (HATS-1339 D3).
    """
    owners = session_owners(entry)
    if owners is None:
        return None  # unusable anchor — already reported; never guess at its owners
    try:
        if not owners:
            aged = entry.stat().st_mtime < cutoff
            return "no owner recorded and older than the TTL" if aged else None
        if any(liveness.is_live(pid, start_time) for pid, start_time in owners):
            return None
        reason = f"owner pid {owners[0][0]} is gone"
        return reason if len(owners) == 1 else f"{reason}, and so is surface child {owners[1][0]}"
    except OSError as exc:
        logger.warning("session-cache sweep skipped %s: %s", entry.name, exc)
    return None


def _drain_workspace_cache(legacy: Path, cutoff: float, liveness: _LazyLiveness) -> None:
    """Delete the pre-HATS-1398 in-tree cache; nothing writes there any more.

    The cache is regenerable, so it is dropped rather than migrated. Only session
    dirs are gated — one may belong to a session that started before the move and
    is still reading it.
    """
    if not legacy.is_dir():
        return
    for entry in legacy.iterdir():
        if entry.name == "sessions":
            _expire_session_dirs(entry, cutoff, liveness)
            _rmdir_quiet(entry)
            continue
        logger.warning("dropping stale in-tree cache %s (HATS-1398)", entry)
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


def _sweep_orphan_project_keys(
    project_dir: Path,
    ttl_days: int = PROJECT_KEY_TTL_DAYS,
    *,
    liveness: _LazyLiveness | None = None,
) -> None:
    """Remove sibling project-key dirs untouched for ``ttl_days`` (HATS-1473).

    ``_sweep_orphan_session_caches`` only walks the CURRENT project's key, so a
    key left by a renamed or one-off project was unreachable for every mechanism
    and accumulated forever — 3939 keys / 16.9 GB measured. The key is a one-way
    hash, so age is all the key itself carries — but the sessions inside name
    their owners, and a key holding a live one is off limits whatever its age
    says (HATS-1339): a long session never touches its key's direct children
    again, so the ttl read it as abandoned and took the running session with it.
    """
    own_key = cache_root(project_dir).name
    cutoff = time.time() - ttl_days * 86400
    session_cutoff = time.time() - SESSION_CACHE_TTL_HOURS * 3600
    liveness = liveness or _LazyLiveness()
    try:
        entries = list(cache_home().iterdir())
    except FileNotFoundError:
        return  # silent-ok: no cache home yet — a first run has nothing to sweep
    except OSError as exc:
        logger.warning("project-key sweep skipped, cache home unreadable: %s", exc)
        return
    for entry in entries:
        if entry.name == own_key or not entry.is_dir():
            continue
        try:
            if _key_last_touched(entry) >= cutoff:
                continue
            if _holds_worktrees(entry):
                logger.info("project cache key %s kept: it holds worktrees", entry.name)
                continue
            live = _live_session_in(entry, liveness, session_cutoff)
            if live is not None:
                logger.info("project cache key %s kept: session %s is running", entry.name, live)
                continue
            shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok regenerable cache
            logger.warning("reclaimed orphaned project cache key: %s", entry.name)
        except OSError as exc:
            logger.warning("project-key sweep skipped %s: %s", entry.name, exc)


def _holds_worktrees(key_dir: Path) -> bool:
    """Whether ``key_dir`` still holds worktree checkouts (HATS-1632).

    Age is no evidence here: a worktree can sit untouched for weeks and still
    carry uncommitted work, and reclaiming it would also strand the git admin
    entry the engine refuses to auto-prune. Presence, not liveness — proving a
    tree is abandoned is the job of the (deliberately manual) gc, not this sweep.
    """
    try:
        return any((key_dir / "worktrees").iterdir())
    except OSError:
        return False  # silent-ok: absent or unreadable — the caller's TTL decides


def _live_session_in(key_dir: Path, liveness: _LazyLiveness, session_cutoff: float) -> str | None:
    """The name of a session under ``key_dir`` that must not be reclaimed.

    An unresolvable owner is not evidence of life — it is what a ``dry-run``
    leaves behind, and pinning on it would hold the key forever. It is not
    evidence of death either, so it gets the SAME TTL grace its own sweep gives
    it: without that, taking the whole key deleted a dir the sibling sweep was
    still protecting.
    """
    sessions = key_dir / "sessions"
    if not sessions.is_dir():
        return None
    for entry in sorted(child for child in sessions.iterdir() if child.is_dir()):
        owners = session_owners(entry)
        if owners is None:
            return entry.name
        if not owners:
            if entry.stat().st_mtime >= session_cutoff:
                return entry.name
            continue
        if any(liveness.is_live(pid, start_time) for pid, start_time in owners):
            return entry.name
    return None


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
        # One process table for both sweeps, read only if one of them needs it.
        liveness = _LazyLiveness()
        _sweep_orphan_session_caches(self.project_dir, liveness=liveness)
        _sweep_orphan_project_keys(self.project_dir, liveness=liveness)
        sweep_runs(self.project_dir, liveness=liveness)

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
                        logger.warning("reclaimed incomplete version residue: %s", residue.name)
                    for orphan in reclaim_orphan_versions(self.project_dir):
                        logger.warning("reclaimed orphaned version: %s", orphan.name)
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
            logger.warning("reclaimed legacy .venv: %s", reclaimed_venv)
