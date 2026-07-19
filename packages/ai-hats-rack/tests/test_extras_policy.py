"""HATS-1035 step 3: ``extras: allow | forbid`` enforced on WRITES only.

A ``forbid`` backlog rejects a Delta.fields Set/Append that targets an
undeclared key; ``allow`` (the packaged default) is today's passthrough. Reads
stay tolerant — a stored card carrying unknown keys never bricks under forbid.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.cardschema import CardSchema, ExtrasForbiddenError, ResolvedField
from ai_hats_rack.dispatch import Append, Delta, Set
from ai_hats_rack.models import TaskCard
from rack_testkit import StubSubscriber, in_lock, make_kernel


def _schema(policy):
    note = ResolvedField("note", "str", True, "", False, None, None, "always")
    return CardSchema([note], extras_policy=policy)


def _writer(op):
    return StubSubscriber(
        "writer", [in_lock("edge:brainstorm--plan")], action=lambda ctx: Delta(fields=op)
    )


# ----- forbid rejects an undeclared write ------------------------------------


def test_forbid_rejects_set_of_undeclared_key(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, schema=_schema("forbid"), subscribers=[_writer({"mystery": Set("x")})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    with pytest.raises(ExtrasForbiddenError) as exc_info:
        kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd)
    assert exc_info.value.field_name == "mystery"
    # aborted before the single persist — the card stayed in brainstorm.
    assert kernel.get("T-1").state == "brainstorm"


def test_forbid_rejects_append_of_undeclared_key(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, schema=_schema("forbid"), subscribers=[_writer({"votes": Append(1)})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    with pytest.raises(ExtrasForbiddenError):
        kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd)


def test_forbid_allows_a_declared_field_and_an_anchor_field(tasks_dir, cwd):
    # 'note' is declared; 'assignee' is a kernel anchor — both writable under forbid.
    subs = [_writer({"note": Set("hi"), "assignee": Set("me")})]
    kernel = make_kernel(tasks_dir, schema=_schema("forbid"), subscribers=subs)
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd).task
    assert card.extras["note"] == "hi"
    assert card.assignee == "me"


# ----- allow is today's passthrough (zero behavior change) -------------------


def test_allow_passes_an_undeclared_write_into_extras(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, schema=_schema("allow"), subscribers=[_writer({"mystery": Set("x")})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd).task
    assert card.extras["mystery"] == "x"


def test_packaged_default_is_allow_and_unchanged(tasks_dir, cwd):
    # Default kernel schema (packaged tasks) is allow — an unknown key rides extras.
    kernel = make_kernel(tasks_dir, subscribers=[_writer({"attachments": Set([1])})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd).task
    assert card.extras["attachments"] == [1]


# ----- reads stay tolerant under forbid --------------------------------------


def test_forbid_never_bricks_reading_a_card_with_unknown_keys(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, schema=_schema("forbid"))
    path = tasks_dir / "T-9" / "task.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("id: T-9\ntitle: legacy\nstate: brainstorm\nunknown_key: kept\n")
    card = kernel.get("T-9")  # a read never consults the write policy
    assert card.extras["unknown_key"] == "kept"


def test_writable_predicate():
    forbid = _schema("forbid")
    assert forbid.writable("note")  # declared
    assert forbid.writable(next(iter(TaskCard._KNOWN_FIELDS)))  # anchor
    assert not forbid.writable("mystery")  # undeclared → not writable
    assert _schema("allow").writable("mystery")  # allow → everything writable
