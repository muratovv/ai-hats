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
  not a bug); ``_safe_replace`` file locks handle the byte-level races.

The ``Migration.run`` callable takes the ``Assembler`` (wrappers need
``self.provider`` / ``agent_dir`` / ``composer.resolver``), so this is an
Assembler-internal registry, not a generic framework — do not import
``MIGRATIONS`` from outside Assembler-aware code. Additive for now; an
``OLDEST_SUPPORTED_STEP`` guard will prune old entries later.
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .constants import AGENT_DIR
from .paths import (
    hooks_dir as _lib_hooks_dir,
    legacy_paths_by_class,
    user_hooks_dir as _user_hooks_dir,
)
from .safe_delete import discard as _safe_discard

if TYPE_CHECKING:
    from .assembler import Assembler

logger = logging.getLogger(__name__)

# Stable banner format — the E2E gate test (HATS-471) greps stderr for
# this prefix to assert the registry actually advanced (or didn't).
# Do not change without updating ``tests/e2e/test_migration_registry_gate.py``
# and the docstring of :func:`run_pending`.
_RUNNING_BANNER = "[ai-hats] running migration step={step} label={label}"


@dataclass(frozen=True)
class Migration:
    """One entry in the migration registry.

    Attributes:
        step: Post-migration value of ``ProjectConfig.migration_step``.
            Strictly monotonic across the registry (each entry has a unique
            step, listed in ascending order).
        run: Callable receiving the :class:`Assembler` instance. Must be
            idempotent and atomically-safe (see module docstring).
        label: Human-readable identifier. Surfaces in the stable banner
            ``"[ai-hats] running migration step=N label=..."`` printed
            to stderr (and emitted via ``logger.info``) — the E2E gate
            test relies on this exact prefix as the spy contract.
    """

    step: int
    run: Callable[["Assembler"], None]
    label: str


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
    from .providers import get_provider

    provider = get_provider(a.project_config.provider)
    a._migrate_claude_md_to_v3(provider)


def _m_migrate_layout_v4(a: "Assembler") -> None:
    migrate_layout_v4(a)


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
        managed_dst.mkdir(parents=True, exist_ok=True)
        try:
            entries = list(legacy.iterdir())
        except OSError:
            entries = []

        for entry in entries:
            if entry.name in whitelist:
                target = managed_dst / entry.name
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
    if managed_dst.is_dir():
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
            shutil.move(str(entry), str(target))


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
        label="claude.md → v3 scaffold",
    ),
    Migration(
        step=6,
        run=_m_migrate_layout_v4,
        label="layout v4 (sessions+tracker+library)",
    ),
]


def latest_step() -> int:
    """Highest ``step`` in the registry — the value a fully-migrated
    project should carry. Use this for seeding greenfield projects and
    for completeness assertions in tests.
    """
    return MIGRATIONS[-1].step


def run_pending(assembler: "Assembler") -> int:
    """Run every registry entry with ``step > current_step``.

    Persists ``migration_step`` after each successful entry so a partial
    failure leaves the project at the last good step (the next ``bump``
    resumes from there). The runner does **not** catch exceptions —
    callers see the original failure with its stack.

    Returns the number of migrations actually executed (0 when the project
    was already at ``latest_step``).
    """
    cfg = assembler.project_config
    ran = 0
    for migration in MIGRATIONS:
        if cfg.migration_step >= migration.step:
            continue
        # Print to stderr (not ``logger.info``) so the banner surfaces
        # regardless of subprocess logging config — the rest of the
        # codebase uses the same channel for one-shot WARN/NOTE rows
        # (``_strip_deprecated_fields``, ``_heal_default_role``, etc.).
        # Also emitted via ``logger.info`` so structured callers can
        # capture it through standard logging.
        banner = _RUNNING_BANNER.format(
            step=migration.step,
            label=migration.label,
        )
        print(banner, file=sys.stderr)
        logger.info(banner)
        migration.run(assembler)
        # Transactional mutation: bump the in-memory counter and persist
        # in one step. If persistence raises (disk full, read-only fs,
        # `_safe_replace` failure), roll back so in-memory state matches
        # on-disk truth. Prevents the "in-memory ahead of disk" drift
        # any caller relying on ``cfg.migration_step`` after a partial
        # bump would otherwise observe.
        prev_step = cfg.migration_step
        cfg.migration_step = migration.step
        try:
            assembler._persist_migration_step(migration.step)
        except Exception:
            cfg.migration_step = prev_step
            raise
        ran += 1
    return ran
