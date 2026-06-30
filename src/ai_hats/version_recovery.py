"""Crash-recovery sweep for the versioned install layout (HATS-648 / R1).

An ``ai-hats self update`` killed mid-pip leaves an incomplete ``versions/<sha>/``
(no ``.complete`` sentinel). This removes that residue so ``versions/`` stays
bounded — **idempotently and conservatively**: never touches ``current`` or any
**complete** dir (a complete dir may be a live pinned run's env — reclaiming
those is R2's job, HATS-649), only removes residue older than a TTL (no liveness
signal exists for a half-install, so age is the stand-in), and deletes via
``safe_delete.discard``. Called at the ``create_session`` chokepoint and at
``self update`` start, with the same age guard at both.

Phase B (HATS-653) adds ``reclaim_legacy_venv``: once this process runs from a
complete versioned venv, the orphaned pre-versioning ``<ai_hats_dir>/.venv`` is
dead weight and is reclaimed (reversible — backed by the launcher self-heal).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import safe_delete
from .paths import ai_hats_dir, is_complete, read_current_sha, versions_root
from .version_refs import current_run_sha, load_refs, ref_is_live

# Reused from the HATS-294 session-cache sweep: conservative 24h window. The
# risk an "incomplete" dir is actually an install in flight lasts seconds, so
# 24h errs heavily toward never deleting a live build. Not a CLI knob (no
# current use case); R2 may revisit alongside its retention policy.
DEFAULT_TTL_HOURS = 24


def sweep_incomplete_versions(
    project_dir: Path, ttl_hours: int = DEFAULT_TTL_HOURS
) -> list[Path]:
    """Remove incomplete ``versions/<sha>/`` residue older than ``ttl_hours``.

    Idempotent and conservative. Returns the list of removed directories (for
    no-silent-caps logging by the caller). A second call is a no-op.
    """
    root = versions_root(project_dir)
    if not root.exists():
        return []
    current = read_current_sha(project_dir)
    cutoff = time.time() - ttl_hours * 3600
    removed: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue  # the 'current' pointer file and any stray files
        sha = entry.name
        if sha == ".refs":
            continue  # liveness-ref store (HATS-649), not a version dir
        if sha == current:
            continue  # never touch the active version
        if is_complete(project_dir, sha):
            continue  # complete → R2's liveness-based reclaim, not ours
        try:
            if entry.stat().st_mtime >= cutoff:
                continue  # within TTL → may be an install in flight
        except OSError:
            continue  # vanished or unstattable — leave it for the next pass
        # Incomplete, aged out, not current → crash residue. Reclaim it.
        safe_delete.discard(
            entry,
            reason="incomplete versioned-install residue (HATS-648)",
            project_dir=project_dir,
        )
        removed.append(entry)
    return removed


def reclaim_orphan_versions(
    project_dir: Path, keep_shas: set[str] | None = None
) -> list[Path]:
    """Reclaim complete, non-``current`` ``versions/<sha>/`` dirs with no live ref.

    **Reclaim-on-certain-death** (HATS-649 / R2): a complete version is removed
    iff it is not the active ``current`` and no **live** liveness ref pins it.
    Liveness is decided by ``root_pid`` + OS ``start_time`` (see
    :func:`ai_hats.version_refs.ref_is_live`) — single-host, **no TTL**: a reused
    pid mismatches the recorded start_time, so a dead run is dead with certainty.

    ``keep_shas`` is an explicit protection set for shas that are not yet
    ``current`` but must survive this pass — e.g. the ``target_sha`` a ``self
    update`` is about to install/reuse (it exists as a complete non-current dir
    before the ``current`` flip, so without this guard the reclaim would delete
    the very dir the update reuses).

    Dead refs (pid gone, or reused → start_time mismatch) are deleted in the same
    pass, so refs never leak. Conservative — any live ref, the ``current``
    version, a ``keep_shas`` entry, an incomplete dir (owned by
    :func:`sweep_incomplete_versions`), or the ``.refs`` store itself is left
    untouched. The legacy ``.venv`` lives outside ``versions/`` and is never
    considered (its reclaim is HATS-653).

    Idempotent. Returns reclaimed dirs for no-silent-caps logging by the caller.
    """
    root = versions_root(project_dir)
    if not root.exists():
        return []
    current = read_current_sha(project_dir)
    keep = keep_shas or set()

    # Partition refs into live (protect their sha) and dead (reclaim the ref).
    live_shas: set[str] = set()
    for ref_path, ref in load_refs(project_dir):
        if ref_is_live(ref):
            sha = ref.get("sha")
            if isinstance(sha, str):
                live_shas.add(sha)
        else:
            ref_path.unlink(missing_ok=True)  # safe-delete: ok dead-ref (drop dead run's ref pointer, no leak)

    removed: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue  # the 'current' pointer file and any stray files
        sha = entry.name
        if sha == ".refs":
            continue  # liveness-ref store, not a version dir
        if sha == current:
            continue  # active version — never reclaim
        if sha in keep:
            continue  # explicitly protected (e.g. self update's target_sha)
        if not is_complete(project_dir, sha):
            continue  # incomplete residue → sweep_incomplete_versions owns it
        if sha in live_shas:
            continue  # a live run pins it
        # Complete, not current, no live ref → orphaned. Reclaim it.
        safe_delete.discard(
            entry,
            reason="orphaned versioned-install (HATS-649)",
            project_dir=project_dir,
        )
        removed.append(entry)
    return removed


def reclaim_legacy_venv(project_dir: Path) -> Path | None:
    """Reclaim the legacy ``<ai_hats_dir>/.venv`` once versioned is authoritative.

    Phase B (HATS-653): after lazy migration to the versioned layout the old
    ``.venv`` only resolves as a fallback (when ``versions/current`` is absent or
    broken). Once this process runs from a complete versioned venv it is dead
    weight whose fallback value only decays, so reclaim it.

    Single guard: ``current_run_sha(project_dir) is not None`` — a non-None result
    proves the running interpreter's ``sys.prefix`` is ``versions/<sha>/``, which
    in one predicate means (a) we are NOT running from ``.venv`` (so discarding it
    can't pull the rug from the live interpreter), (b) no ``AI_HATS_VENV`` / yaml
    override / editable checkout is active (all resolve to None), and (c) we run
    from a working versioned prefix. When it fails, ``.venv`` is kept.

    Reversible, not destructive: if a versioned install later breaks, the
    launcher's ``heal_if_needed`` recreates ``.venv`` on the next ``self update``.
    Removal goes through :func:`safe_delete.discard` (a missing ``.venv`` is a
    no-op). Returns the reclaimed path (for caller logging) or ``None`` when
    skipped.
    """
    if current_run_sha(project_dir) is None:
        return None  # running from .venv / override / editable → keep legacy venv
    legacy = ai_hats_dir(project_dir) / ".venv"
    if not (legacy.exists() or legacy.is_symlink()):
        return None  # already reclaimed or never migrated → no-op
    safe_delete.discard(
        legacy,
        reason="legacy .venv superseded by versioned install (HATS-653)",
        project_dir=project_dir,
    )
    return legacy
