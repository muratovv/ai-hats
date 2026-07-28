"""HATS-1275: state-conditional field gates — ``required_on`` (entering the state
with the field empty is refused) and ``write_on`` (the field may only CHANGE on a
transition entering one of those states).

Enforced by ONE kernel check, post-sequence and pre-persist, against the RESULTING
card — not an ``on_enter`` handler (argv order) and not the bespoke kwarg (``--set``
is a second write path). Rationale: tasks/HATS-1275/plan.md §Requirements R3.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from rack_testkit import make_kernel

from ai_hats_rack.cardschema import StateGateError, build_card_schema
from ai_hats_rack.cli import main
from ai_hats_rack.definition import BacklogDefinitionError, load_backlog
from ai_hats_rack.fsm import InvalidTransitionError
from ai_hats_rack.models import TaskCard
from ai_hats_rack.ops import parse_ops


def _write(tmp_path, fields: str) -> object:
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: t\nprefix: T\n"
        "fsm:\n"
        "  initial: open\n"
        "  states: [{name: open}, {name: shut}]\n"
        "  edges:\n"
        "    - {from: open, to: shut}\n"
        "links:\n"
        "  kinds:\n"
        "    - {name: parent_task}\n"
        f"fields:\n{fields}"
    )
    return load_backlog(doc)


def _spec(defn, name: str):
    return next(f for f in defn.fields if f.name == name)


# ----- S1: the grammar carries the declarations ------------------------------


def test_required_on_parses_onto_the_field_spec(tmp_path):
    defn = _write(tmp_path, "  - {name: why, type: str, default: '', required_on: [shut]}\n")
    assert _spec(defn, "why").required_on == ("shut",)


def test_field_without_declarations_defaults_to_unconstrained(tmp_path):
    defn = _write(tmp_path, "  - {name: why, type: str, default: ''}\n")
    spec = _spec(defn, "why")
    assert spec.required_on == ()
    assert spec.write_on == ()


def test_gate_naming_an_unknown_state_fails_closed(tmp_path):
    # A typo'd state would silently disable the guard — the exact silent-failure
    # class this card exists to close.
    with pytest.raises(BacklogDefinitionError) as exc_info:
        _write(tmp_path, "  - {name: why, type: str, default: '', required_on: [shutt]}\n")
    assert "shutt" in str(exc_info.value)


# ----- S2: required_on — an administrative close must record why --------------


def _card(tmp_path):
    kernel = make_kernel(tmp_path)
    return kernel, kernel.create(actor="t", caller_cwd=tmp_path, title="Probe").task.id


def test_cancel_without_resolution_is_refused(tmp_path):
    kernel, tid = _card(tmp_path)
    with pytest.raises(StateGateError) as exc_info:
        kernel.transition(tid, "cancelled", actor="t", caller_cwd=tmp_path)
    assert "resolution" in str(exc_info.value)
    assert kernel._load(tid).state == "brainstorm", "refusal must leave zero bytes changed"


def test_cancel_with_resolution_passes(tmp_path):
    kernel, tid = _card(tmp_path)
    kernel.transition(tid, "cancelled", actor="t", caller_cwd=tmp_path, resolution="duplicate")
    assert kernel._load(tid).state == "cancelled"


def _ops(*tokens):
    return parse_ops(tokens, field_types={"resolution": "str", "final_state": "str"})


def test_cancel_without_resolution_is_refused_on_the_set_path_too(tmp_path):
    # The bypass R3 exists to close: a kwarg-side check would pass this through.
    kernel, tid = _card(tmp_path)
    with pytest.raises(StateGateError):
        kernel.transition_ops(tid, _ops("--state", "cancelled"), actor="t", caller_cwd=tmp_path)
    assert kernel._load(tid).state == "brainstorm"


def test_cancel_satisfied_by_a_later_set_op_in_the_same_composite(tmp_path):
    # THE case that kills the on_enter-handler design: the resolution arrives
    # AFTER the state op in argv order. A handler would refuse this legitimate
    # command; the post-sequence check accepts it.
    kernel, tid = _card(tmp_path)
    kernel.transition_ops(
        tid,
        _ops("--state", "cancelled", "--set", "resolution=duplicate"),
        actor="t",
        caller_cwd=tmp_path,
    )
    card = kernel._load(tid)
    assert (card.state, card.resolution) == ("cancelled", "duplicate")


# ----- S2: write_on — the review summary is meaningless on any other edge -----


def _walk(kernel, tid, *states, cwd):
    for state in states:
        kernel.transition(tid, state, actor="t", caller_cwd=cwd)


def test_final_state_on_a_non_review_edge_is_refused(tmp_path):
    kernel, tid = _card(tmp_path)
    with pytest.raises(StateGateError) as exc_info:
        kernel.transition(tid, "plan", actor="t", caller_cwd=tmp_path, final_state="shipped it")
    assert "final_state" in str(exc_info.value)
    assert kernel._load(tid).state == "brainstorm"


def test_final_state_on_a_non_review_edge_is_refused_on_the_set_path_too(tmp_path):
    kernel, tid = _card(tmp_path)
    with pytest.raises(StateGateError):
        kernel.transition_ops(
            tid, _ops("--state", "plan", "--set", "final_state=x"), actor="t", caller_cwd=tmp_path
        )


def test_final_state_with_no_state_op_at_all_is_refused(tmp_path):
    # A bare field write is not a transition entering `review` either.
    kernel, tid = _card(tmp_path)
    with pytest.raises(StateGateError):
        kernel.transition_ops(
            tid, _ops("--set", "final_state=x"), actor="t", caller_cwd=tmp_path
        )


def test_final_state_on_the_review_edge_passes(tmp_path):
    kernel, tid = _card(tmp_path)
    _walk(kernel, tid, "plan", "execute", "document", cwd=tmp_path)
    kernel.transition(tid, "review", actor="t", caller_cwd=tmp_path, final_state="shipped it")
    assert kernel._load(tid).final_state == "shipped it"


def test_an_unrelated_later_transition_does_not_trip_the_gate(tmp_path):
    # The gate fires on CHANGE, not on presence — a card legitimately carrying
    # final_state must still be able to move on.
    kernel, tid = _card(tmp_path)
    _walk(kernel, tid, "plan", "execute", "document", cwd=tmp_path)
    kernel.transition(tid, "review", actor="t", caller_cwd=tmp_path, final_state="shipped it")
    kernel.transition(tid, "done", actor="t", caller_cwd=tmp_path)
    assert kernel._load(tid).state == "done"


# ----- R6 regression pin: the guard the card filed as missing already exists ---


def test_same_state_transition_is_already_refused_both_paths(tmp_path):
    # HATS-1275 was filed claiming rack lacked this. It does not — kernel.py
    # refuses under force, and the FSM refuses without it. Pinned so the
    # already-shipped behaviour cannot regress unnoticed.
    kernel, tid = _card(tmp_path)
    with pytest.raises(InvalidTransitionError):
        kernel.transition(tid, "brainstorm", actor="t", caller_cwd=tmp_path)
    with pytest.raises(ValueError, match="already in state"):
        kernel.transition(
            tid, "brainstorm", actor="t", caller_cwd=tmp_path, force=True, reason="probe"
        )


# ----- R4: the gates are declaration-driven, not `cancelled`/`review` hardcodes -


def test_gates_are_driven_by_the_declaration_on_a_foreign_state_set(tmp_path):
    defn = _write(tmp_path, "  - {name: why, type: str, default: '', required_on: [shut]}\n")
    tasks = tmp_path / "cards"
    tasks.mkdir()
    kernel = make_kernel(
        tasks,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        schema=build_card_schema(defn),
    )
    tid = kernel.create(actor="t", caller_cwd=tasks, title="Foreign").task.id

    with pytest.raises(StateGateError) as exc_info:
        kernel.transition(tid, "shut", actor="t", caller_cwd=tasks)
    assert "why" in str(exc_info.value)

    kernel.transition_ops(
        tid,
        parse_ops(("--state", "shut", "--set", "why=because"), field_types={"why": "str"}),
        actor="t",
        caller_cwd=tasks,
    )
    assert kernel._load(tid).state == "shut"


# ----- S4: the read-back that makes write_on verifiable end-to-end ------------


def test_context_renders_final_state(tmp_path):
    # Without this the write_on guard has no human read-back and its e2e cannot
    # assert the recorded value (HATS-1263 filed it alongside the guard).
    card = TaskCard(id="HATS-1", title="reviewable", state="review", final_state="shipped it")
    path = tmp_path / "tasks" / "HATS-1" / "task.yaml"
    path.parent.mkdir(parents=True)
    card.save(path)

    result = CliRunner().invoke(main, ["context", "HATS-1", "--tasks-dir", str(tmp_path / "tasks")])

    assert result.exit_code == 0, result.output
    assert "final_state: shipped it" in result.output
