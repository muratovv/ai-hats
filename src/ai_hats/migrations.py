"""Schema-versioned migration registry (HATS-471).

Stop accumulating migration code forever. Migrations run **once per project**,
gated by ``ProjectConfig.migration_step``. Each entry advances the counter on
success; subsequent invocations skip entries whose step is already covered.

After HATS-469 the registry is replayed by :meth:`Assembler._refresh` when
called with ``install_time=True`` (the path used by ``Assembler.init`` and
the ``do_bump`` CLI pipeline). The ``set_role`` runtime bootstrap path
uses ``install_time=False`` and skips the registry — migrations are
expected to have run during the user-initiated init/bump.

``migration_step`` is **orthogonal** to ``schema_version``:

* ``schema_version`` describes the on-disk yaml format (handled in
  :func:`ProjectConfig.from_yaml`).
* ``migration_step`` is a monotonic counter for one-shot side effects
  performed at install-time refresh (file moves, cleanups, content heals).

Two are independent: bumping yaml schema does not automatically advance
``migration_step``, and adding a new migration entry does not require a yaml
schema bump.

**Migration contract** (callers MUST honour):

* **Idempotent.** Calling the function on already-migrated state is a no-op.
  The runner cannot guarantee single execution under all conditions (partial
  failures, concurrent bumps, direct invocation from other Assembler entry
  points — see "scope" below), so each entry must defend itself.
* **Atomically-safe.** If a migration may fail mid-way, it must leave the
  on-disk state consistent enough that a re-run completes the job. The
  runner does not roll back; it advances ``migration_step`` only after the
  function returns successfully.
* **Concurrency-tolerant.** Under N parallel install-time refreshes
  (``ai-hats self init`` / ``self update`` / ``_bump_internal``) on the
  same project, a migration may execute up to N times. Idempotency must
  hold for that case — file locks at the ``_safe_replace`` level handle
  most byte-level races, but two processes reading ``migration_step=0``
  and both replaying step 1 is the steady-state expectation, not a bug.
  HATS-469 widened the concurrency surface from bump-only to all three
  install-time entry-points; the idempotency contract is unchanged.

**Scope of the "at-most-once" guarantee.** The runner gates entries by
``cfg.migration_step``, but the wrapped methods are still ordinary
``Assembler`` instance methods and SOME of them are invoked directly from
other entry points (e.g. ``_migrate_claude_md_to_v3`` runs once during
``Assembler.init`` and again on each ``set_role`` so a fresh project gets
the scaffold without first paying for a bump). The registry guarantees
at-most-once **via** :func:`run_pending`; it does not own all call sites.
This is by design — those direct invocations predate HATS-471 and serve
bootstrap paths the registry cannot cover.

**Assembler coupling.** The ``Migration.run`` callable accepts the
:class:`Assembler` instance, not a generic ``(Path, ProjectConfig)`` tuple,
because the wrapped methods need ``self.provider`` / ``self.agent_dir`` /
``self.composer.resolver``. The wrappers (``_m_*`` below) are pure dispatch
to existing Assembler methods. This is a refactor of the pre-HATS-471
inline migration calls (which lived in the now-removed ``Assembler.bump``
method) into a registry, **not** a generic framework — do not import
``MIGRATIONS`` from outside Assembler-aware code.

The registry is intentionally additive: when a migration is no longer needed
in supported releases, a follow-up will delete the entry and introduce an
``OLDEST_SUPPORTED_STEP`` guard. Until then, every existing project replays
the full list once on first bump after upgrade.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

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
# Each wrapper adapts an existing ``Assembler`` instance method to the
# ``Callable[[Assembler], None]`` registry signature. The bodies stay inside
# ``Assembler`` (they need ``self.project_dir`` / ``self.project_config`` /
# provider lookup); the wrappers are pure dispatch.


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
    a._migrate_layout_v4()


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
            step=migration.step, label=migration.label,
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
