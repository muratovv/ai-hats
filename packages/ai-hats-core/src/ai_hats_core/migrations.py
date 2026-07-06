"""Generic step-gated migration runner (HATS-868 T7).

A ``Ctx``-parametrised primitive extracted from the Assembler-bound
``ai_hats.migrations``. Any stateful package (e.g. the tracker's TaskCard-schema
migrations, T16) reuses this loop without re-implementing the gate / persist /
rollback semantics.

Core owns **no domain policy**: the step read/write and the banner emission are
injected callables, so this module imports no ``ai_hats.*`` symbol and carries
no ``[ai-hats]`` brand string or stderr/logging policy.

**Migration contract** (each entry MUST honour — the runner never rolls back a
body and advances the step only after the body returns):

* **Idempotent.** Re-running on already-migrated state is a no-op.
* **Atomically-safe.** A mid-way failure must leave on-disk state re-runnable.
* **Concurrency-tolerant.** Under N parallel replays an entry may execute up to
  N times.
"""  # comment-length: allow — migration contract for the shared primitive

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

Ctx = TypeVar("Ctx")


@dataclass(frozen=True)
class Migration(Generic[Ctx]):
    """One entry in a step-gated migration registry.

    Attributes:
        step: Post-migration value of the caller's monotonic step counter.
            Strictly monotonic across a registry (unique, listed ascending).
        run: Idempotent, atomically-safe callable receiving the ``Ctx``.
        label: Human-readable identifier surfaced through ``emit_banner``.
    """

    step: int
    run: Callable[[Ctx], None]
    label: str


def latest_step(migrations: Sequence[Migration[Ctx]]) -> int:
    """Highest ``step`` in ``migrations`` — the value a fully-migrated caller
    should carry. Use for seeding greenfield state and completeness assertions.
    """
    return migrations[-1].step


def run_pending(
    ctx: Ctx,
    migrations: Sequence[Migration[Ctx]],
    *,
    read_step: Callable[[Ctx], int],
    set_step: Callable[[Ctx, int], None],
    persist_step: Callable[[Ctx, int], None],
    emit_banner: Callable[[int, str], None] | None = None,
) -> int:
    """Run entries whose ``step`` exceeds the caller's current step, advancing
    the counter transactionally after each; return the count executed (0 when
    already at ``latest_step``).

    Step bindings are injected so the loop stays domain-free: ``read_step(ctx)``
    (current step), ``set_step(ctx, step)`` (in-memory advance), and
    ``persist_step(ctx, step)`` (durable write; may raise). ``set_step`` runs
    *before* ``persist_step`` — some callers assert the in-memory value already
    reflects the step. A persist failure rolls the in-memory advance back to the
    last durable step and re-raises (the next run resumes there); bodies are
    never rolled back. ``emit_banner(step, label)``, if given, fires once per
    executed entry before its body runs.
    """  # comment-length: allow — injected-binding + rollback contract
    ran = 0
    for migration in migrations:
        if read_step(ctx) >= migration.step:
            continue
        if emit_banner is not None:
            emit_banner(migration.step, migration.label)
        migration.run(ctx)
        # Bump in-memory first, then persist; roll back on persist failure so
        # the counter never runs ahead of disk.
        prev_step = read_step(ctx)
        set_step(ctx, migration.step)
        try:
            persist_step(ctx, migration.step)
        except Exception:
            set_step(ctx, prev_step)
            raise
        ran += 1
    return ran
