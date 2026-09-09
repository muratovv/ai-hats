"""Schema-versioned migration registry (HATS-471).

Migrations run **once per project**, gated by ``ProjectConfig.migration_step``
(a monotonic counter for one-shot install-time side effects — file moves,
cleanups, content heals). Orthogonal to ``schema_version`` (the on-disk yaml
format, handled in ``ProjectConfig.from_yaml``): bumping one never implies the
other. Replayed by ``Assembler._refresh(install_time=True)`` (init / do_bump);
the ``set_role`` runtime path skips it.

**Migration contract** (each entry MUST honour — the runner does not roll back,
and advances the step only after the function returns):

* **Idempotent.** Re-running on already-migrated state is a no-op.
* **Atomically-safe.** A mid-way failure must leave on-disk state re-runnable.
* **Concurrency-tolerant.** Under N parallel install-time refreshes a migration
  may execute up to N times (two processes both replaying step 1 is expected,
  not a bug); ``_safe_replace`` file locks handle most byte-level races.

``run_pending`` is not the only call site: some wrapped methods also run
directly from ``init`` / ``set_role``, so idempotency must hold for those direct
invocations, not just the gated replay.

The generic step-gated *runner* now lives in ``ai_hats_core.migrations``
(``Migration[Ctx]`` / ``run_pending`` / ``latest_step``, HATS-868 T7); this
module is its **Assembler-bound instance** — the ``MIGRATIONS`` registry (each
entry's ``run`` takes the ``Assembler`` for ``self.provider`` / ``agent_dir`` /
``composer.resolver``) plus the banner + step-binding adapter below. Do not
import ``MIGRATIONS`` from outside Assembler-aware code. Additive for now; an
``OLDEST_SUPPORTED_STEP`` guard will prune old entries later.
"""  # comment-length: allow — migration-registry contract + core-split note

from __future__ import annotations

import logging
import shutil
import sys
from typing import TYPE_CHECKING

from .constants import (
    AGENT_DIR,
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
)
from .paths import (
    hooks_dir as _lib_hooks_dir,
    legacy_paths_by_class,
    library_dir as _lib_dir,
    user_hooks_dir as _user_hooks_dir,
)
from ai_hats_core.migrations import (
    Migration,
    latest_step as _latest_step,
    run_pending as _run_pending,
)
from ai_hats_core.safe_delete import discard as _safe_discard
from ai_hats_core.safe_delete import replace as _safe_replace

if TYPE_CHECKING:
    from pathlib import Path

    from .assembler import Assembler

logger = logging.getLogger(__name__)


def _recorded_move(src: "Path", dst: "Path", *, reason: str, project_dir: "Path") -> None:
    """Move ``src`` to ``dst`` leaving a safe_delete trash record.

    A bare ``shutil.move`` out of the managed namespace left no snapshot and
    no audit line, so an eviction was only reconstructable from inode ctime
    (HATS-1123). Copy-then-discard so the source is recorded before it goes.
    """
    shutil.copy2(str(src), str(dst))
    _safe_discard(src, reason=reason, project_dir=project_dir)


# Stable banner format — the E2E gate test greps stderr for
# this prefix to assert the registry actually advanced (or didn't).
# Do not change without updating ``tests/e2e/test_migration_registry_gate.py``
# and the docstring of :func:`run_pending`.
_RUNNING_BANNER = "[ai-hats] running migration step={step} label={label}"


# ``Migration`` (the registry-entry dataclass) now lives in
# ``ai_hats_core.migrations``, re-exported above; this module owns only the
# Assembler-bound registry + adapter (HATS-868 T7).


# ----- migration wrappers --------------------------------------------------
#
# Each wrapper adapts a migration to the ``Callable[[Assembler], None]`` registry
# signature. The v3 / v07 migration bodies still live in ``Assembler`` (they need
# ``self.provider`` / ``self.composer.resolver``); the v4-layout *logic* was moved
# here in HATS-715 (``migrate_layout_v4*``, take-``a``), with Assembler keeping
# thin delegators for the tested API.


def _m_normalize_yaml(a: "Assembler") -> None:
    a._normalize_yaml()


def _m_strip_legacy_managed_block(a: "Assembler") -> None:
    a._strip_legacy_managed_block()


def _m_cleanup_obsolete_files(a: "Assembler") -> None:
    # ``_cleanup_obsolete_files`` is a staticmethod taking the project dir.
    from .assembler import Assembler as _A

    _A._cleanup_obsolete_files(a.project_dir)


def _m_heal_external_refs(a: "Assembler") -> None:
    from .migration_healer import heal_external_refs

    heal_external_refs(a.project_dir)


def _m_migrate_claude_md_to_v3(a: "Assembler") -> None:
    """Retired by HATS-1201 — kept as a no-op because step numbers are bound
    to the on-disk counter and must never be reordered or renumbered. The v3
    scaffold it used to write no longer exists (HATS-1170); step 7 removes
    what it left behind."""
    del a


def _m_migrate_layout_v4(a: "Assembler") -> None:
    migrate_layout_v4(a)


def _strip_marked_block(text: str, start: str, end: str) -> str:
    """Remove one ``start``…``end`` block plus the blank line it leaves behind."""
    if start not in text or end not in text:
        return text
    head = text[: text.index(start)]
    tail = text[text.index(end) + len(end) :]
    if head.strip() and tail.strip():
        return head.rstrip("\n") + "\n\n" + tail.lstrip("\n")
    return (head + tail).strip("\n")


def _m_strip_orphaned_claude_scaffold(a: "Assembler") -> None:
    """Drop the ai-hats block HATS-1170 orphaned in root ``CLAUDE.md``.

    Root ``CLAUDE.md`` is user territory now, so only ai-hats's own markers go
    — the aggregator block and the legacy uppercase injection block; whatever
    the user wrote around them survives byte-for-byte, and a file left as pure
    whitespace was all ours and goes entirely. ``CLINE.md`` / ``GEMINI.md``
    are untouched: their blocks under the same markers are still live.
    HATS-1203 removed HATS-1201's skip-while-user-rules-exist gate — the
    composed prompt carries user-rules now, so this block delivers nothing.
    """
    claude_md = a.project_dir / "CLAUDE.md"
    if not claude_md.is_file():
        return

    existing = claude_md.read_text()
    stripped = _strip_marked_block(existing, PUBLISH_AGGREGATOR_START, PUBLISH_AGGREGATOR_END)
    stripped = _strip_marked_block(stripped, INJECTION_START, INJECTION_END)
    if stripped == existing:
        return

    if not stripped.strip():
        _safe_discard(claude_md, reason="claude-md-scaffold-drop", project_dir=a.project_dir)
        return

    _safe_replace(
        claude_md,
        (stripped.rstrip("\n") + "\n").encode("utf-8"),
        reason="claude-md-scaffold-drop",
        project_dir=a.project_dir,
    )


def _m_retire_agy_token_zeros(a: "Assembler") -> None:
    """Withdraw the token counts agy never emitted (HATS-1397).

    agy has no usage field, so every ``tokens`` block ever written for it is a
    placeholder — and it read as a measurement: on the real archive 119 of 237
    agy sessions answered ``is_zero_output`` with True, one of them a 55-turn
    session. ``session backfill`` cannot repair them, because agy never receives
    a provider session id and its attribution guard refuses all 237. Nothing is
    re-derived here; the claim is simply withdrawn, and the counters agy did
    measure (``turns``/``tool_calls``) stay.
    """  # comment-length: allow — names the measurement that justifies a data migration
    import json

    from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY, METRICS_JSON

    from .paths import runs_dir

    runs = runs_dir(a.project_dir)
    if not runs.is_dir():
        return

    for metrics_path in sorted(runs.glob(f"session_*/{METRICS_JSON}")):
        try:
            record = json.loads(metrics_path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("provider") != "agy":
            continue
        flags = record.get("flags")
        flags = [f for f in flags if isinstance(f, str)] if isinstance(flags, list) else []
        if FLAG_NO_TOKEN_TELEMETRY in flags:
            continue
        tokens = record.get("tokens")
        # Defensive: a non-zero block would be telemetry from somewhere, and
        # dropping it would destroy a measurement rather than a placeholder.
        if isinstance(tokens, dict) and any(tokens.values()):
            continue

        record.pop("tokens", None)
        record["flags"] = [*flags, FLAG_NO_TOKEN_TELEMETRY]
        _safe_replace(
            metrics_path,
            (json.dumps(record, indent=2) + "\n").encode("utf-8"),
            reason="agy-token-zeros-retired",
            project_dir=a.project_dir,
        )


# ----- v4-layout migration logic (HATS-715: moved out of Assembler) --------
#
# Take the Assembler for shared helpers (a._idempotent_move /
# a._safe_discard_with_warn / a._ai_hats_owned_hook_basenames); the migration
# sequencing lives here. Assembler keeps thin delegators for the tested API.


def migrate_layout_v4(a: "Assembler") -> None:
    """HATS-471: unified v3→v4 layout migration entry-point.

    Consolidates the three historical splits — sessions / tracker / library —
    into a single call site so the migration registry has one entry per
    logical migration (not three for the same v4 layout move).

    The three sub-methods stay as private helpers (they remain
    independently testable and the split is convenient for narrow log
    diagnostics), but no other caller invokes them directly.
    """
    migrate_layout_v4_sessions(a)
    migrate_layout_v4_tracker(a)
    migrate_layout_v4_library(a)


def migrate_layout_v4_library(a: "Assembler") -> None:
    """One-shot migration of library-mirror artefacts (HATS-314).

    Moves `.agent/{rules,skills,hooks}/` → `<ai_hats_dir>/library/...`.
    `.claude/skills/` and `.githooks/` are NOT touched — they stay as
    copy-publish targets owned by external tooling.

    HATS-549 Phase 4: the ``.agent/hooks/`` entry is partitioned
    before the generic move — managed files (basename in the
    ai-hats-owned whitelist) head to ``<ai_hats_dir>/library/hooks/``
    as before; foreign files (anything else, including subdirs)
    head to ``<ai_hats_dir>/user-hooks/``. Keeps user-owned content
    out of the managed namespace where future sweep passes could
    delete it.
    """
    migrate_layout_v4_hooks_partition(a)
    for old_abs, new_abs in legacy_paths_by_class(a.project_dir, "library"):
        # The hooks pair was handled by the partition step; skip
        # so ``_idempotent_move`` doesn't run on the now-empty
        # ``.agent/hooks/`` directory (the partition leaves it
        # cleaned up).
        if old_abs.name == "hooks" and old_abs.parent.name == AGENT_DIR:
            continue
        a._idempotent_move(old_abs, new_abs)


def migrate_layout_v4_hooks_partition(a: "Assembler") -> None:
    """HATS-549 Phase 4: partition legacy ``.agent/hooks/`` and reconcile
    pre-Phase-4 stuck states. Two passes:

    1. **Legacy partition** — route each ``.agent/hooks/`` entry by basename
       whitelist: ai-hats-owned hooks → ``<ai_hats_dir>/library/hooks/``,
       everything else (subdirs, arbitrary files) → ``<ai_hats_dir>/user-hooks/``.
    2. **Managed-namespace reconciliation** — move foreign files left in
       ``library/hooks/`` by a pre-549 auto-heal out to ``user-hooks/`` (except
       framework bookkeeping like ``.manifest``), so the next bump cleanly heals
       stuck states.

    Idempotent (a fully-partitioned project no-ops; its empty ``.agent/hooks/`` is
    dropped). Destination collisions route through ``_safe_discard`` (recoverable);
    discard failures WARN to stderr — silence would mask a partial-state limbo
    (review S.4).
    """
    managed_dst = _lib_hooks_dir(a.project_dir)
    user_dst = _user_hooks_dir(a.project_dir)
    whitelist = a._ai_hats_owned_hook_basenames()

    # --- Pass 1: legacy partition ---
    legacy = a.project_dir / AGENT_DIR / "hooks"
    if legacy.is_dir():
        try:
            entries = list(legacy.iterdir())
        except OSError:
            entries = []

        for entry in entries:
            if entry.name in whitelist:
                _safe_discard(entry, reason="hooks-partition-retired", project_dir=a.project_dir)
            else:
                user_dst.mkdir(parents=True, exist_ok=True)
                target = user_dst / entry.name
                if target.exists():
                    a._safe_discard_with_warn(
                        entry,
                        reason="hooks-partition-collision",
                    )
                    continue
                shutil.move(str(entry), str(target))

        try:
            if not any(legacy.iterdir()):
                _safe_discard(
                    legacy,
                    reason="hooks-partition-cleanup",
                    project_dir=a.project_dir,
                )
        except OSError as e:
            print(
                f"[ai-hats] WARN: hooks-partition: could not clean up empty {legacy}: {e}",
                file=sys.stderr,
            )

    # --- Pass 2: managed-namespace reconciliation ---
    # If a previous-version bump auto-healed settings.json to point
    # at .agent/ai-hats/library/hooks/<x> AND moved the file there,
    # the file is currently sitting in the managed namespace where
    # any future framework-side sweep could mistake it for managed
    # content and discard it. Move it out NOW, while we're already
    # in a "rearrange hooks" frame.
    # This pass is the ONLY one keyed on ai_hats_dir rather than
    # project_dir, so an AI_HATS_DIR override aimed at another checkout made it
    # evict THAT project's hooks. providers.py computes the same containment
    # predicate but only warns; here it must skip.
    if not managed_dst.resolve().is_relative_to(a.project_dir.resolve()):
        print(
            f"[ai-hats] WARN: hooks-reconcile: {managed_dst} is outside "
            f"{a.project_dir} (AI_HATS_DIR override) — skipping (HATS-1123).",
            file=sys.stderr,
        )
    elif managed_dst.is_dir():
        try:
            managed_entries = list(managed_dst.iterdir())
        except OSError:
            managed_entries = []
        for entry in managed_entries:
            # Skip framework bookkeeping and whitelisted basenames.
            if entry.name == ".manifest":
                continue
            if entry.name in whitelist:
                continue
            user_dst.mkdir(parents=True, exist_ok=True)
            target = user_dst / entry.name
            if target.exists():
                a._safe_discard_with_warn(
                    entry,
                    reason="hooks-reconcile-collision",
                )
                continue
            _recorded_move(entry, target, reason="hooks-reconcile", project_dir=a.project_dir)


def migrate_layout_v4_tracker(a: "Assembler") -> None:
    """One-shot migration of tracker + root-class artefacts (HATS-313).

    Moves backlog/, hypotheses/, decisions/, STATE.md, and .last_backup
    from their legacy .agent/ locations to <ai_hats_dir>/tracker/* (and
    the framework-root entries STATE.md / .last_backup directly under
    <ai_hats_dir>/). Idempotent on a re-run after success.
    """
    for class_ in ("tracker", "root"):
        for old_abs, new_abs in legacy_paths_by_class(a.project_dir, class_):
            a._idempotent_move(old_abs, new_abs)


def migrate_layout_v4_sessions(a: "Assembler") -> None:
    """One-shot migration of session-class artefacts to <ai_hats_dir>/sessions/.

    Moves seven legacy locations (pipeline_runs, retrospectives, audits,
    handoffs, experiments, worktrees, worktree.json) plus an orphan
    handoff file. Idempotent: a no-op once every legacy path is gone.
    See ADR `2026-05-13-hats-316-ai-hats-dir-layout.md`.
    """
    for old_abs, new_abs in legacy_paths_by_class(a.project_dir, "sessions"):
        a._idempotent_move(old_abs, new_abs)
    # Pick up the orphan handoff file lingering at .agent/ root.
    orphan = a.project_dir / AGENT_DIR / "handoff-2026-04-09-hats-061.md"
    if orphan.exists():
        from .paths import handoffs_dir

        dest_dir = handoffs_dir(a.project_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / orphan.name
        if not dest.exists():
            shutil.move(str(orphan), str(dest))
        else:
            try:
                _safe_discard(
                    orphan,
                    reason="layout-v4-orphan",
                    project_dir=a.project_dir,
                )
            except OSError:
                pass


# ----- registry ------------------------------------------------------------
#
# Ordered by ``step`` ascending. Append new entries at the bottom; never
# reorder or renumber existing ones (the counter on disk is bound to them).


def _m_drop_retired_wt_hooks(a: "Assembler") -> None:
    """Discard the retired ``library/wt-hooks/`` tree (HATS-1269).

    Worktree hooks spawn in place from the declaring skill, so nothing writes or
    sweeps this dir any more and an upgraded project would keep a managed tree
    with no owner. Only what the manifest claimed is removed — an unmanaged file
    beside it is somebody's, and the dir goes only once it is empty.
    """
    from .sweeper import read_marker_names

    retired = _lib_dir(a.project_dir) / "wt-hooks"
    manifest = retired / ".manifest"
    if not retired.is_dir():
        return
    for name in sorted(read_marker_names(manifest)):
        _safe_discard(retired / name, reason="retire-wt-hooks", project_dir=a.project_dir)
    _safe_discard(manifest, reason="retire-wt-hooks-manifest", project_dir=a.project_dir)
    try:
        retired.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        logger.warning(
            "wt-hooks retirement: %s still holds files ai-hats never managed — left in place",
            retired,
        )


def _m_drop_retired_runtime_hooks(a: "Assembler") -> None:
    """Discard the retired ``library/hooks/`` tree (HATS-1480).

    Runtime hooks live in session cache (HATS-1268), so nothing writes or
    sweeps this dir any more and an upgraded project would keep a managed tree
    with no owner. Only what the manifest claimed is removed — an unmanaged file
    beside it is somebody's, and the dir goes only once it is empty.
    """
    from .sweeper import read_marker_names

    retired = _lib_dir(a.project_dir) / "hooks"
    manifest = retired / ".manifest"
    if not retired.is_dir():
        return
    for name in sorted(read_marker_names(manifest)):
        _safe_discard(retired / name, reason="retire-runtime-hooks", project_dir=a.project_dir)
    _safe_discard(manifest, reason="retire-runtime-hooks-manifest", project_dir=a.project_dir)
    try:
        retired.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        logger.warning(
            "hooks retirement: %s still holds files ai-hats never managed — left in place",
            retired,
        )


MIGRATIONS: list[Migration] = [
    Migration(
        step=1,
        run=_m_normalize_yaml,
        label="yaml normalize (strip deprecated fields)",
    ),
    Migration(
        step=2,
        run=_m_strip_legacy_managed_block,
        label="gitignore HATS-317 cleanup",
    ),
    Migration(
        step=3,
        run=_m_cleanup_obsolete_files,
        label="obsolete files cleanup",
    ),
    Migration(
        step=4,
        run=_m_heal_external_refs,
        label="heal external refs HATS-397",
    ),
    Migration(
        step=5,
        run=_m_migrate_claude_md_to_v3,
        label="claude.md → v3 scaffold (retired, no-op)",
    ),
    Migration(
        step=6,
        run=_m_migrate_layout_v4,
        label="layout v4 (sessions+tracker+library)",
    ),
    Migration(
        step=7,
        run=_m_strip_orphaned_claude_scaffold,
        label="drop orphaned claude.md scaffold HATS-1201",
    ),
    Migration(
        step=8,
        run=_m_retire_agy_token_zeros,
        label="retire fabricated agy token zeros HATS-1397",
    ),
    Migration(
        step=9,
        run=_m_drop_retired_wt_hooks,
        label="drop retired library/wt-hooks HATS-1269",
    ),
    Migration(
        step=10,
        run=_m_drop_retired_runtime_hooks,
        label="drop retired library/hooks HATS-1480",
    ),
]


def latest_step() -> int:
    """Highest ``step`` in the registry — the value a fully-migrated project
    should carry. Seeds greenfield projects and completeness assertions.
    """
    return _latest_step(MIGRATIONS)


def _set_migration_step(assembler: "Assembler", step: int) -> None:
    assembler.project_config.migration_step = step


def _emit_banner(step: int, label: str) -> None:
    """Dual-channel migration banner (the E2E gate's stderr spy contract).

    ``print(file=sys.stderr)`` surfaces regardless of subprocess logging config;
    ``logger.info`` lets structured callers capture the same line.
    """
    banner = _RUNNING_BANNER.format(step=step, label=label)
    print(banner, file=sys.stderr)
    logger.info(banner)


def run_pending(assembler: "Assembler") -> int:
    """Run every registry entry with ``step > migration_step`` via the generic
    ``ai_hats_core.migrations.run_pending``.

    Binds the Assembler's config counter (read / in-memory set / persist) and
    the stderr+logger banner to the domain-free core runner. Persists after each
    entry so a partial failure resumes from the last good step on the next
    ``bump``; exceptions propagate with their stack. Returns the number of
    entries executed (0 when already at ``latest_step``).

    Refuses outright on a dir with no ``ai-hats.yaml`` (HATS-1123): the config
    loader returns defaults for a missing file and ``migration_step`` defaults
    to 0, so an uninitialised dir — a git worktree, since ai-hats.yaml is
    gitignored — replayed the entire registry. ``init`` seeds the counter for
    greenfield before refreshing; every other caller has no such guard.
    """
    if not assembler.config_path.exists():
        logger.debug("migrations: no %s — registry skipped", assembler.config_path)
        return 0
    return _run_pending(
        assembler,
        MIGRATIONS,
        read_step=lambda a: a.project_config.migration_step,
        set_step=_set_migration_step,
        persist_step=lambda a, step: a._persist_migration_step(step),
        emit_banner=_emit_banner,
    )
