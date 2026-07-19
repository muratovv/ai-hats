"""HATS-1035 step 2: schema-driven create defaults + write-strict validation.

Reads stay tolerant (an old card violating choices still loads); writes are
strict — create validates every field it sets, a transition only the fields it
touches, and an unknown validator name fails closed AT COMPOSITION.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.cardschema import (
    CardSchema,
    FieldValidationError,
    RequiredFieldError,
    ResolvedField,
    UnknownValidatorError,
    build_card_schema,
    default_card_schema,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.models import TaskCard
from rack_testkit import make_kernel


def _field(name, type="str", *, has_default=True, default="", required=False,
           choices=None, validator=None, emit="always"):
    return ResolvedField(name, type, has_default, default, required, choices, validator, emit)


# ----- CardSchema.validate (type / choices / validator) ----------------------


def test_validate_accepts_valid_choice():
    schema = CardSchema([_field("priority", default="medium", choices=("low", "high"))])
    schema.validate("priority", "high")  # no raise


def test_validate_rejects_bad_choice_naming_the_set():
    schema = CardSchema([_field("priority", default="medium", choices=("low", "medium", "high"))])
    with pytest.raises(FieldValidationError) as exc_info:
        schema.validate("priority", "bogus")
    err = exc_info.value
    assert err.field_name == "priority"
    assert err.details["choices"] == ["low", "medium", "high"]
    assert "low" in str(err) and "bogus" in str(err)


def test_validate_rejects_type_mismatch():
    schema = CardSchema([_field("tags", type="list", default=[])])
    with pytest.raises(FieldValidationError, match="expects list"):
        schema.validate("tags", "not-a-list")


def test_validate_rejects_bool_for_int():
    schema = CardSchema([_field("count", type="int", default=0)])
    with pytest.raises(FieldValidationError):
        schema.validate("count", True)


def test_validate_undeclared_field_is_a_noop():
    # Read tolerance: an undeclared name is governed by extras policy, not here.
    CardSchema([]).validate("whatever", object())


def test_validate_runs_the_bound_validator():
    def reject_short(value):
        if len(value) < 3:
            raise ValueError("too short")

    schema = CardSchema([_field("code", validator=reject_short)])
    schema.validate("code", "abcd")
    with pytest.raises(FieldValidationError, match="too short"):
        schema.validate("code", "ab")


# ----- resolve_create (defaults + required) ----------------------------------


def test_resolve_create_fills_schema_defaults_for_unset_inputs():
    schema = default_card_schema()
    out = schema.resolve_create({"description": None, "priority": None, "tags": None})
    assert out == {"description": "", "priority": "medium", "tags": []}


def test_resolve_create_validates_provided_values():
    schema = default_card_schema()
    with pytest.raises(FieldValidationError):
        schema.resolve_create({"priority": "bogus"})


def test_resolve_create_enforces_required():
    schema = CardSchema([_field("hypothesis", has_default=False, required=True)])
    with pytest.raises(RequiredFieldError):
        schema.resolve_create({})  # no default, not provided → refused


# ----- build_card_schema: validator resolution fails closed at composition ---


def _defn_with_validator(tmp_path, validator_line):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: t\nprefix: T\n"
        "fsm:\n  initial: brainstorm\n  states: [{name: brainstorm}, {name: document}]\n"
        "  edges: [{from: brainstorm, to: document}, {from: document, to: brainstorm}]\n"
        "links:\n  kinds: [{name: parent_task}]\n"
        f"fields:\n  - {{name: votes, type: any, {validator_line}}}\n"
    )
    return load_backlog(doc)


def test_build_card_schema_binds_known_validator(tmp_path):
    defn = _defn_with_validator(tmp_path, "validator: v")
    seen = []
    schema = build_card_schema(defn, {"v": lambda value: seen.append(value)})
    schema.validate("votes", [1])
    assert seen == [[1]]


def test_build_card_schema_unknown_validator_fails_closed_naming_it(tmp_path):
    defn = _defn_with_validator(tmp_path, "validator: no-such")
    with pytest.raises(UnknownValidatorError) as exc_info:
        build_card_schema(defn, {})  # empty registry → fail closed at composition
    err = exc_info.value
    assert err.validator == "no-such"
    assert err.field_name == "votes"
    assert "no-such" in str(err)


def test_packaged_schema_declares_no_validators():
    # The zero-config path: build with an empty registry, no UnknownValidatorError.
    schema = build_card_schema(load_backlog(), {})
    assert all(f.validator is None for f in schema.fields)


# ----- kernel.create: title-only + write-strict ------------------------------


def test_create_title_only_uses_schema_defaults(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    card = kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="just a title").task
    assert card.title == "just a title"
    assert card.priority == "medium"  # schema default
    assert card.reviewer == "user"
    assert card.description == "" and card.role == "" and card.tags == []


def test_create_empty_title_is_a_typed_refusal(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    with pytest.raises(RequiredFieldError) as exc_info:
        kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="   ")
    assert exc_info.value.field_name == "title"


def test_create_bad_choice_is_a_typed_refusal(tasks_dir, cwd):
    # Net-new: choices were enforced NOWHERE before HATS-1035.
    kernel = make_kernel(tasks_dir)
    with pytest.raises(FieldValidationError) as exc_info:
        kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t", priority="urgent")
    assert exc_info.value.details["choices"] == ["low", "medium", "high", "critical"]
    # nothing was written — the refusal is pre-lock
    assert not (tasks_dir / "T-1").exists()


def test_create_default_args_are_byte_identical(tasks_dir, cwd):
    # The None-sentinel resolution must reproduce today's card exactly (R5).
    kernel = make_kernel(tasks_dir)
    card = kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="demo").task
    reference = TaskCard(id="T-1", title="demo", state=card.state, created=card.created,
                         updated=card.updated)
    assert card.to_dict() == reference.to_dict()


# ----- transition: only touched fields validated -----------------------------


def test_transition_validates_touched_resolution_field(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="t")
    # resolution is a declared str field — a str value passes write-strict.
    result = kernel.transition(
        "T-1", "plan", actor="t", caller_cwd=cwd, resolution="looks good"
    )
    assert result.task.resolution == "looks good"


# ----- read tolerance: an old card violating choices still loads -------------


def test_old_card_with_bad_choice_still_loads(tasks_dir):
    path = tasks_dir / "T-9" / "task.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("id: T-9\ntitle: legacy\nstate: review\npriority: urgent\n")
    card = TaskCard.from_yaml(path)  # reads never brick on a stale choice
    assert card.priority == "urgent"
