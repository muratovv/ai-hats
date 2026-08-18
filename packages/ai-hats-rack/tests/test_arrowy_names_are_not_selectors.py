"""User-authored names may hold an arrow, and they are still NAMES (HATS-1719).

A link kind, and an edge name, are free text the loader accepts as-is. The
selector grammar arrived with two sniffers — ``Subscription.__post_init__`` and
``Dispatcher.subscribers_for`` — and both mistook such a name for an arrow: the
first would swallow it into the FSM path where it can never fire, the second
raised inside the task lock where it used to answer "nobody subscribes".

Mutation-proven absent before this file: dropping ``is_event_key`` from
``parse_selector`` left 996 + 138 tests green.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import Phase, Subscription
from ai_hats_rack.ops import parse_ops
from ai_hats_rack.selectors import parse_selector
from rack_testkit import make_kernel

_DOC = (
    "name: b\nprefix: T\n"
    "fsm:\n"
    "  initial: plan\n"
    "  states: [{name: plan}, {name: execute}]\n"
    "  edges: [{from: plan, to: execute}]\n"
    "links:\n"
    "  kinds:\n"
    "    - {name: 'a->b', arity: many}\n"
)


def test_a_link_op_on_an_arrowy_kind_still_works(tmp_path, cwd):
    """The end-to-end shape: the loader accepts the kind, so the kernel must too."""
    doc = tmp_path / "cat" / "backlog.yaml"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_DOC)
    definition = load_backlog(doc)

    kernel = make_kernel(
        tmp_path / "tasks",
        topology=definition.topology,
        registry=definition.links_registry,
        subscribers=[],
    )
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-2", title="b")

    kernel.transition_ops("T-1", parse_ops(["--link", "a->b:T-2"]), actor="t", caller_cwd=cwd)


@pytest.mark.parametrize("key", ["link:a->b", "unlink:a->b", "read:a->b", "op:a->b"])
def test_a_namespaced_key_holding_an_arrow_is_not_parsed_as_one(key):
    """The unit-level statement of the same fact, at the call site that crashed."""
    assert parse_selector(key) is None
    assert Subscription(key, Phase.IN_LOCK).selector == key
