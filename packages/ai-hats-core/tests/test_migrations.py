"""Unit tests for :mod:`ai_hats_core.migrations` — the generic step-gated
migration runner (HATS-868 T7).

Proves the runner is unbound from ``Assembler``: the ``Ctx`` here is a plain
dataclass with a list standing in for disk persistence — never an ``ai_hats``
symbol. Covers ordering, the already-migrated no-op, mid-registry resume, the
transactional persist-failure rollback, banner emission, and ``latest_step``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ai_hats_core.migrations import Migration, latest_step, run_pending


@dataclass
class FakeCtx:
    """Non-Assembler migration context: an in-memory step + a persistence sink."""

    step: int = 0
    persisted: list[int] = field(default_factory=list)
    ran: list[int] = field(default_factory=list)
    fail_persist_at: int | None = None

    def persist(self, step: int) -> None:
        if self.fail_persist_at == step:
            raise OSError("disk full")
        self.persisted.append(step)


def _registry() -> list[Migration[FakeCtx]]:
    return [
        Migration(step=1, run=lambda c: c.ran.append(1), label="one"),
        Migration(step=2, run=lambda c: c.ran.append(2), label="two"),
        Migration(step=3, run=lambda c: c.ran.append(3), label="three"),
    ]


def _run(ctx: FakeCtx, migrations, banner=None) -> int:
    return run_pending(
        ctx,
        migrations,
        read_step=lambda c: c.step,
        set_step=lambda c, s: setattr(c, "step", s),
        persist_step=lambda c, s: c.persist(s),
        emit_banner=banner,
    )


def test_runs_pending_in_order_and_advances_step():
    ctx = FakeCtx(step=0)
    ran = _run(ctx, _registry())
    assert ran == 3
    assert ctx.ran == [1, 2, 3]
    assert ctx.step == 3
    assert ctx.persisted == [1, 2, 3]


def test_already_migrated_is_noop():
    ctx = FakeCtx(step=3)
    ran = _run(ctx, _registry())
    assert ran == 0
    assert ctx.ran == []
    assert ctx.step == 3
    assert ctx.persisted == []


def test_partial_start_resumes_from_current_step():
    ctx = FakeCtx(step=1)
    ran = _run(ctx, _registry())
    assert ran == 2
    assert ctx.ran == [2, 3]
    assert ctx.step == 3
    assert ctx.persisted == [2, 3]


def test_persist_failure_rolls_back_inmemory_step_and_reraises():
    ctx = FakeCtx(step=0, fail_persist_at=2)
    with pytest.raises(OSError, match="disk full"):
        _run(ctx, _registry())
    # migration 2's body ran, its in-memory advance was rolled back to the
    # last durably-persisted step, and step 1 is on disk (2 never landed).
    assert ctx.ran == [1, 2]
    assert ctx.step == 1
    assert ctx.persisted == [1]


def test_emit_banner_receives_step_and_label_per_executed_entry():
    ctx = FakeCtx(step=1)
    seen: list[tuple[int, str]] = []
    _run(ctx, _registry(), banner=lambda step, label: seen.append((step, label)))
    assert seen == [(2, "two"), (3, "three")]


def test_emit_banner_none_is_accepted():
    ctx = FakeCtx(step=0)
    # No banner callback → runs without error.
    assert _run(ctx, _registry(), banner=None) == 3


def test_latest_step_returns_highest():
    assert latest_step(_registry()) == 3
