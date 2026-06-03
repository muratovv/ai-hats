"""Crash-recovery sweep for the versioned install layout (HATS-648 / R1).

An ``ai-hats self update`` killed mid-pip leaves an incomplete
``versions/<sha>/`` dir — one that lacks the ``.complete`` sentinel (written
last, only after a fully-successful install+verify). This module removes that
residue so ``versions/`` stays bounded, **idempotently and conservatively**:

- never touches ``current`` or any **complete** dir — reclaiming a complete but
  orphaned version requires liveness and is R2's job (HATS-649); a complete dir
  may be a live pinned run's frozen env;
- only removes residue older than a TTL, so an install **in flight** is not
  deleted out from under a concurrent process (there is no liveness signal until
  R2 — the age guard is the stand-in);
- removal goes through :func:`safe_delete.discard` (idempotent; hard-deletes
  under ``$TMPDIR``, trashes elsewhere with a forensic manifest entry).

Called at the ``create_session`` chokepoint (converges on any ``ai-hats``
invocation) and at ``self update`` start (cleans before staging the next
build) — the **same** age guard applies at both call-sites.

R2 (HATS-649) adds :func:`reclaim_orphan_versions` to this module: liveness-
based reclaim of **complete** dirs that the incomplete sweep deliberately leaves
alone. The two passes are complementary — incomplete residue is reclaimed by
age (no liveness signal exists for a half-install), complete versions only when
a liveness ref proves no live run pins them.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import safe_delete
from .paths import is_complete, read_current_sha, versions_root
from .version_refs import load_refs, ref_is_live

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
            ref_path.unlink(missing_ok=True)  # dead run → drop its ref, no leak

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
