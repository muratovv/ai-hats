"""HATS-1035 step 5: ``Delta.fields`` validated against the schema.

A subscriber's Set/Append on a DECLARED field is checked (type/choices/
validator) in ``_delta_applier`` — validated on the RESULTING value, so the
model container gate still fires first and an Append is judged by the list it
yields. Undeclared names stay a no-op (read tolerance). The epic-automation and
force paths are unaffected — epic emits work_log only.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.cardschema import CardSchema, FieldValidationError, ResolvedField
from ai_hats_rack.composition import build_bound_subscribers, stock_factories
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import Append, Delta, Set
from ai_hats_rack.extensions import EpicAutomationExtension
from ai_hats_rack.fsm import load_topology
from rack_testkit import CollectingSink, StubSubscriber, in_lock, make_kernel, walk


def _rf(name, ftype="str", *, default="", choices=None, validator=None):
    return ResolvedField(name, ftype, True, default, False, choices, validator, "always")


def _writer(op, edge="edge:brainstorm--plan"):
    return StubSubscriber("writer", [in_lock(edge)], action=lambda ctx: Delta(fields=op))


# ----- illegal Set on a choices field: typed abort, journaled, nothing writ --


def test_illegal_set_on_choices_field_aborts_journals_nothing_persisted(tasks_dir, cwd):
    sink = CollectingSink()
    # Default packaged schema: priority choices are enforced on the delta too.
    kernel = make_kernel(
        tasks_dir, subscribers=[_writer({"priority": Set("urgent")})], journal_sink=sink
    )
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    with pytest.raises(FieldValidationError) as exc_info:
        kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd)
    assert exc_info.value.field_name == "priority"
    assert exc_info.value.details["choices"] == ["low", "medium", "high", "critical"]
    assert kernel.get("T-1").state == "brainstorm"  # nothing persisted
    assert sink.records[-1].result == "aborted"  # the refusal stays auditable


# ----- legal Set + Append pass -----------------------------------------------


def test_legal_set_and_append_pass(tasks_dir, cwd):
    schema = CardSchema(
        [_rf("priority", default="medium", choices=("low", "medium", "high")),
         _rf("votes", "any", default=[])]
    )
    op = {"priority": Set("high"), "votes": Append({"session": "s1"})}
    kernel = make_kernel(tasks_dir, schema=schema, subscribers=[_writer(op)])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd).task
    assert card.priority == "high"
    assert card.extras["votes"] == [{"session": "s1"}]


def test_append_to_a_declared_list_field_is_judged_by_the_resulting_list(tasks_dir, cwd):
    # Regression: a scalar entry appended to a type:list field must pass — the
    # schema validates the produced list, not the entry (test_kernel pin 470).
    schema = CardSchema([_rf("tags", "list", default=[])])
    kernel = make_kernel(tasks_dir, schema=schema, subscribers=[_writer({"tags": Append("x")})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd).task
    assert card.tags == ["x"]


# ----- a bound validator runs on the delta -----------------------------------


def test_validator_runs_on_a_delta_set(tasks_dir, cwd):
    def reject_short(value):
        if len(value) < 3:
            raise ValueError("too short")

    schema = CardSchema([_rf("code", validator=reject_short)])
    kernel = make_kernel(tasks_dir, schema=schema, subscribers=[_writer({"code": Set("ab")})])
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    with pytest.raises(FieldValidationError, match="too short"):
        kernel.transition("T-1", "plan", actor="t", caller_cwd=cwd)
    assert kernel.get("T-1").state == "brainstorm"


# ----- force relaxes the FSM arrow only, not schema validation ---------------


def test_force_path_applies_a_valid_delta_field(tasks_dir, cwd):
    # brainstorm → done is a forced (non-topology) edge; a valid delta still applies.
    kernel = make_kernel(
        tasks_dir, subscribers=[_writer({"priority": Set("critical")}, "edge:brainstorm--done")]
    )
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    card = kernel.transition(
        "T-1", "done", actor="t", caller_cwd=cwd, force=True, reason="ship it"
    ).task
    assert card.state == "done" and card.priority == "critical"


def test_force_does_not_bypass_schema_validation(tasks_dir, cwd):
    kernel = make_kernel(
        tasks_dir, subscribers=[_writer({"priority": Set("urgent")}, "edge:brainstorm--done")]
    )
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    with pytest.raises(FieldValidationError):
        kernel.transition("T-1", "done", actor="t", caller_cwd=cwd, force=True, reason="ship it")
    assert kernel.get("T-1").state == "brainstorm"  # force never weakens the write gate


# ----- epic-automation is unaffected (work_log only) -------------------------


def test_epic_automation_flow_unaffected(tasks_dir, cwd):
    topo = load_topology()
    automation = EpicAutomationExtension(topology=topo)
    lifecycle = [
        s
        for s in build_bound_subscribers(load_backlog(), tasks_dir, stock_factories())
        if s.name in ("stamp-lifecycle", "clear-lifecycle")
    ]
    kernel = make_kernel(tasks_dir, topology=topo, subscribers=[automation, *lifecycle])
    automation.bind(kernel)
    kernel.create(actor="test", caller_cwd=cwd, task_id="EPIC", title="Epic")
    walk(kernel, "EPIC", "plan", "execute", cwd=cwd)
    kernel.create(actor="test", caller_cwd=cwd, task_id="C-1", title="child", parent_task="EPIC")
    # Walking the child to done stamps completed_at (a declared-field Set on the
    # delta) and auto-advances the epic — both flow through the validating applier.
    walk(kernel, "C-1", "plan", "execute", "document", "review", "done", cwd=cwd)
    child = kernel.get("C-1")
    assert child.state == "done" and child.completed_at  # stamp-lifecycle Set applied
    assert kernel.get("EPIC").state == "review"  # epic auto-advanced (work_log delta)
