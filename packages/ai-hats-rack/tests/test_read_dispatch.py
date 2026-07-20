"""READ-phase dispatch (HATS-1064): ``Dispatcher.run_read`` aggregates each
read subscriber's :class:`ReadContribution` in priority order, fires only for
the event's ``read:<kind>`` key, returns empty with no subscribers, and is
fail-soft — a raising enricher is surfaced as a visible error block, never
dropped and never crashing the read."""

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.dispatch import (
    DispatchContext,
    Dispatcher,
    Phase,
    ReadContribution,
    Subscription,
)
from ai_hats_rack.events import ReadEvent
from ai_hats_rack.linked import build_context
from ai_hats_rack.models import TaskCard


class StubReader:
    """A read subscriber: subscribes to ``read:<kind>`` and returns a fixed
    body (or raises, to exercise fail-soft)."""

    def __init__(self, name, kind, body=None, *, raises=False, priority=100):
        self.name = name
        self._kind = kind
        self._body = body
        self._raises = raises
        self._priority = priority

    def subscriptions(self):
        return [Subscription(f"read:{self._kind}", Phase.READ, self._priority)]

    def on_read(self, ctx):
        if self._raises:
            raise RuntimeError("boom")
        return ReadContribution(self.name, self._body)


def _make_ctx(cwd: Path, kind: str = "parent_task"):
    def factory() -> DispatchContext:
        return DispatchContext(
            event=ReadEvent(kind),
            task=TaskCard(id="T-1", title="child"),
            caller_cwd=cwd,
            is_epic=False,
            actor="read",
        )

    return factory


def test_run_read_aggregates_contributions_in_priority_order(cwd):
    disp = Dispatcher(
        [
            StubReader("second", "parent_task", "B", priority=20),
            StubReader("first", "parent_task", "A", priority=10),
        ]
    )
    out = disp.run_read(ReadEvent("parent_task"), _make_ctx(cwd))
    assert [(c.name, c.body) for c in out] == [("first", "A"), ("second", "B")]


def test_run_read_no_subscribers_returns_empty(cwd):
    assert Dispatcher([]).run_read(ReadEvent("parent_task"), _make_ctx(cwd)) == []


def test_run_read_fires_only_for_the_events_kind(cwd):
    disp = Dispatcher([StubReader("dep", "depends_on", "D")])
    # a read of parent_task must not invoke a depends_on reader
    assert disp.run_read(ReadEvent("parent_task"), _make_ctx(cwd)) == []


def test_run_read_is_fail_soft_and_surfaces_the_error(cwd):
    disp = Dispatcher(
        [
            StubReader("boom", "parent_task", raises=True, priority=10),
            StubReader("ok", "parent_task", "A", priority=20),
        ]
    )
    out = disp.run_read(ReadEvent("parent_task"), _make_ctx(cwd))
    # the raising enricher is surfaced (not dropped); the healthy one still runs
    assert out[0].name == "boom"
    assert "failed" in out[0].body
    assert (out[1].name, out[1].body) == ("ok", "A")


# ----- build_context threading (W1 slice 2) -----------------------------------


def _card(tasks_dir: Path, task_id: str, **fields) -> None:
    card = TaskCard(id=task_id, **fields)
    path = tasks_dir / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)


def test_build_context_unenriched_when_no_read_subscribers(tasks_dir):
    _card(tasks_dir, "T-1", title="parent")
    _card(tasks_dir, "T-2", title="child", parent_task="T-1")
    pkg = build_context(tasks_dir, "T-2")
    # regression guard: no read subscribers → no enrichments, and --json omits it
    assert pkg.enrichments == ()
    assert "enrichments" not in pkg.to_dict()


def test_build_context_runs_read_enricher_for_present_parent_kind(tasks_dir):
    _card(tasks_dir, "T-1", title="parent")
    _card(tasks_dir, "T-2", title="child", parent_task="T-1")
    reader = StubReader("parent-req", "parent_task", "REQUIREMENTS")
    pkg = build_context(tasks_dir, "T-2", read_subscribers=[reader])
    assert [(c.name, c.body) for c in pkg.enrichments] == [("parent-req", "REQUIREMENTS")]
    assert pkg.to_dict()["enrichments"] == [{"name": "parent-req", "body": "REQUIREMENTS"}]


def test_build_context_read_enricher_not_fired_without_the_link(tasks_dir):
    # a top-level card (no parent_task link) → read:parent_task never fires;
    # the dispatch loop only visits kinds PRESENT on the card (no self-filter).
    _card(tasks_dir, "T-1", title="parent")
    reader = StubReader("parent-req", "parent_task", "REQUIREMENTS")
    pkg = build_context(tasks_dir, "T-1", read_subscribers=[reader])
    assert pkg.enrichments == ()
