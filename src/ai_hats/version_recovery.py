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

Phase B (HATS-653) adds :func:`reclaim_legacy_venv`: once **this** process runs
from a complete versioned venv, the orphaned pre-versioning
``<ai_hats_dir>/.venv`` (outside ``versions/``, which both R1 and R2 leave
alone) is dead weight and is reclaimed. Guarded by ``current_run_sha``; backed
by the launcher's existing self-heal, so the reclaim is reversible.
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

    Phase B (HATS-653): after lazy migration to the versioned layout the
    pre-versioning ``.venv`` only ever resolves as a *fallback* (the launcher /
    :func:`ai_hats.paths.venv_path` precedence consults it solely when
    ``versions/current`` is absent or broken). Once **this** process runs from a
    complete versioned venv, ``.venv`` is dead weight whose fallback value only
    decays — every ``self update`` it misses leaves it staler. Reclaim it.

    The single guard is ``current_run_sha(project_dir) is not None``. A non-None
    result proves the running interpreter's ``sys.prefix`` is ``versions/<sha>/``,
    which in one predicate means:

    * **(a)** we are *not* running from ``.venv`` — so discarding it cannot pull
      the rug out from under the live interpreter (lazy imports / a cross-device
      trash-move could otherwise crash the current command). This assumes
      ``.venv`` is a real directory, the only state migration produces
      (``python3 -m venv``); a hypothetical ``.venv`` symlinked *into*
      ``versions/`` would resolve under ``versions/`` and is out of scope;
    * **(b)** no ``AI_HATS_VENV`` / yaml ``venv_path`` override and no editable /
      dev checkout is active — all of those resolve ``current_run_sha → None``
      (:func:`ai_hats.version_refs.current_run_sha`), so a user-owned venv is
      never touched;
    * **(c)** we run from a ``versions/<sha>``-shaped prefix — which for a *live*
      interpreter is necessarily a working venv (one cannot execute from a
      non-existent prefix). The guard itself is a lexical ``sys.prefix`` check,
      not a ``.complete`` attestation; completeness is incidental to being live.

    When the guard fails, the legacy ``.venv`` is kept. This is *stronger* than a
    bare ``read_current_sha`` check: running-from-versioned additionally proves
    live-process safety and the absence of an override.

    Reclaim is **reversible**, not a destructive last resort: if a versioned
    install later breaks and ``.venv`` is gone, the launcher's ``heal_if_needed``
    recreates the default ``.venv`` on the next ``ai-hats self update`` and the
    python self-update rebuilds the versioned install. So this is bounded disk
    hygiene backed by the existing self-heal. Removal goes through
    :func:`safe_delete.discard` (idempotent — a missing ``.venv`` is a no-op).

    Returns the reclaimed path (for no-silent-caps logging by the caller) or
    ``None`` when the guard skips or ``.venv`` was already absent.
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
