"""Link/unlink dispatch (HATS-1043 step 6, ADR-0017 §3): a declared
``links.kinds[].handlers`` handler fires IN-LOCK on link/unlink of that kind
(keys ``link:<kind>`` / ``unlink:<kind>``); an abort rolls the mutation back
before persist; a kind WITHOUT handlers dispatches nothing (no event storm on
the packaged default); and each kind handler subscribes exactly once per event.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.composition import (
    build_bound_subscribers,
    build_link_subscribers,
    compose_subscribers,
    stock_factories,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import AbortOperation, OperationAborted, Phase
from ai_hats_rack.extensions import standalone_extensions
from ai_hats_rack.ops import parse_ops

from rack_testkit import make_kernel

_DOC = (
    "name: b\nprefix: T\n"
    "fsm:\n"
    "  initial: plan\n"
    "  states: [{name: plan}, {name: execute}, {name: document}]\n"
    "  edges: [{from: plan, to: execute}, {from: execute, to: document}]\n"
    "links:\n"
    "  kinds:\n"
    "    - {name: parent_task, arity: one, inverse: children}\n"
    "    - {name: depends_on, arity: many, aliases: [depends], handlers: [dep-check]}\n"
    "    - {name: related, arity: many, inverse: related}\n"
    "    - {name: children, derived: true, inverse: parent_task}\n"
)


class _Recorder:
    """A stub kind handler that records the link events it sees; may abort."""

    name = "dep-check"
    PHASE = Phase.IN_LOCK

    def __init__(self, abort: bool = False) -> None:
        self._abort = abort
        self.events: list = []
        self.seen_depends: list[list[str]] = []

    def on_event(self, ctx):
        self.events.append(ctx.event)
        self.seen_depends.append(list(ctx.task.depends_on))
        if self._abort:
            raise AbortOperation("dep-check says no")
        return None


def _defn(tmp_path: Path):
    doc = tmp_path / "cat" / "backlog.yaml"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_DOC)
    return load_backlog(doc)


def _kernel_with(tmp_path: Path, recorder: _Recorder):
    defn = _defn(tmp_path)
    factories = {**stock_factories(), "dep-check": lambda d, c, cfg: recorder}
    subs = compose_subscribers(defn, tmp_path / "tasks", factories)
    return make_kernel(
        tmp_path / "tasks",
        topology=defn.topology,
        registry=defn.links_registry,
        subscribers=subs,
    )


def _two_cards(kernel, cwd):
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-2", title="b")


def test_declared_kind_handler_fires_on_link_and_unlink(tmp_path, cwd):
    rec = _Recorder()
    k = _kernel_with(tmp_path, rec)
    _two_cards(k, cwd)

    k.transition_ops("T-1", parse_ops(["--link", "depends:T-2"]), actor="t", caller_cwd=cwd)
    (linked,) = rec.events
    assert (linked.key, linked.kind, linked.target, linked.removed) == (
        "link:depends_on",
        "depends_on",
        "T-2",
        False,
    )

    k.transition_ops("T-1", parse_ops(["--unlink", "depends:T-2"]), actor="t", caller_cwd=cwd)
    unlinked = rec.events[-1]
    assert (unlinked.key, unlinked.removed) == ("unlink:depends_on", True)


def test_kind_handler_sees_the_mutated_card_and_journals(tmp_path, cwd):
    rec = _Recorder()
    k = _kernel_with(tmp_path, rec)
    _two_cards(k, cwd)
    res = k.transition_ops(
        "T-1", parse_ops(["--link", "depends:T-2"]), actor="t", caller_cwd=cwd
    )
    # the handler's ctx.task carries the just-added link (mutate → dispatch order)
    assert rec.seen_depends == [["T-2"]]
    # link event rides the existing DispatchRecord machinery
    assert [r.event_key for r in res.journal] == ["link:depends_on"]
    assert res.journal[0].result == "persisted"


def test_kind_handler_abort_rolls_the_link_back(tmp_path, cwd):
    rec = _Recorder(abort=True)
    k = _kernel_with(tmp_path, rec)
    _two_cards(k, cwd)
    with pytest.raises(OperationAborted) as exc:
        k.transition_ops("T-1", parse_ops(["--link", "depends:T-2"]), actor="t", caller_cwd=cwd)
    assert exc.value.event_key == "link:depends_on"
    assert exc.value.subscriber == "dep-check"
    assert "T-2" not in k.get("T-1").depends_on  # nothing persisted


def test_kind_without_handlers_dispatches_nothing(tmp_path, cwd):
    # The packaged default declares NO kind handlers — a link must fire no link
    # event (no subscribers, no journal record) yet still apply (zero behavior
    # change; guards against an event storm on every link).
    tasks = tmp_path / "tasks"
    k = make_kernel(tasks, subscribers=standalone_extensions(tasks))
    _two_cards(k, cwd)
    res = k.transition_ops(
        "T-1", parse_ops(["--link", "related:T-2"]), actor="t", caller_cwd=cwd
    )
    assert res.journal == ()  # no edge, no link event → empty journal
    assert res.ops[0]["changed"] is True  # the link itself still happened
    assert "T-2" in k.get("T-1").related


def test_kind_handler_subscribes_once_per_link_event(tmp_path):
    defn = _defn(tmp_path)
    subs = build_link_subscribers(
        defn, tmp_path, {"dep-check": lambda d, c, cfg: _Recorder()}
    )
    dep = [s for s in subs if s.name == "dep-check"]
    assert len(dep) == 1  # one channel — not one per (kind, verb)
    keys = [s.event_key for s in dep[0].subscriptions()]
    assert sorted(keys) == ["link:depends_on", "unlink:depends_on"]
    assert len(keys) == len(set(keys))  # no double-subscription
    # kind handlers are the LINK builder's job — the state/edge builder ignores
    # kind_handlers, so no handler is built (and would fire) twice.
    bound = build_bound_subscribers(
        defn, tmp_path, {**stock_factories(), "dep-check": lambda d, c, cfg: _Recorder()}
    )
    assert not any(s.name == "dep-check" for s in bound)
