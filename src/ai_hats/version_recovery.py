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
build) — the **same** age guard applies at both call-sites. R2 extends this
module with liveness-based reclaim of complete dirs; it does not duplicate it.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import safe_delete
from .paths import is_complete, read_current_sha, versions_root

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
