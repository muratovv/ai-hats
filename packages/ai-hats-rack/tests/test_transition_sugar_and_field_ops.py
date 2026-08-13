"""HATS-1036 step 3: transition edge-name sugar + --set/--append field ops.

The positional/`--state` token accepts a declared edge NAME resolved against the
card's current state (wrong state → invalid_transition; a state-name collision →
a typed load-time refusal). `--set <field>=<value>` / `--append <field>=<json>`
ride the op stream onto FieldsOp (Set/Append) with the existing schema
validation — int --set fields coerce, else string; malformed input is typed.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack import cli, ops
from ai_hats_rack.cli import main
from ai_hats_rack.definition import EdgeNameStateCollisionError, load_backlog
from ai_hats_rack.dispatch import Append, Set
from ai_hats_rack.ops import FieldsOp, OpParseError, StateOp, parse_ops

# A full task topology (so the wired integrator's epic-automation, if installed,
# finds its required states) whose only custom field is an int — to exercise
# --set int coercion end to end.
_INT_BACKLOG = """\
name: tasks
prefix: HATS
fsm:
  initial: brainstorm
  states:
    - { name: brainstorm }
    - { name: plan }
    - { name: execute }
    - { name: document }
    - { name: review }
    - { name: done }
    - { name: blocked }
    - { name: cancelled }
  edges:
    - { from: brainstorm, to: plan }
    - { from: brainstorm, to: cancelled }
    - { from: plan, to: execute }
    - { from: execute, to: document }
    - { from: document, to: review }
    - { from: review, to: done }
    - { from: brainstorm, to: blocked }
    - { from: blocked, to: brainstorm }
links:
  kinds:
    - { name: parent_task, arity: one }
fields:
  - { name: budget, type: int, default: 0 }
"""


def _args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def _create(runner, tmp_path, *extra):
    return runner.invoke(main, ["create", "c", *extra, *_args(tmp_path), "--json"])


# ----- edge-name sugar (CLI, packaged tasks: reopen = done->execute) ----------


def test_named_edge_resolves_to_its_target_from_the_current_state(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    for state in ("execute", "document", "review", "done"):
        runner.invoke(
            main, ["transition", "HATS-001", state, "--force", "--reason", "w", *_args(tmp_path)]
        )
    out = runner.invoke(main, ["transition", "HATS-001", "reopen", *_args(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["task"]["state"] == "execute"  # reopen: done -> execute
    assert payload["transitions"] == [
        {"task_id": "HATS-001", "from": "done", "to": "execute", "reason": ""}
    ]


def test_named_edge_from_the_wrong_state_is_invalid_transition(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)  # sits in brainstorm; `reopen` starts at done
    out = runner.invoke(main, ["transition", "HATS-001", "reopen", *_args(tmp_path), "--json"])
    assert out.exit_code == 1
    error = json.loads(out.stdout)["error"]
    assert error["code"] == "invalid_transition"
    assert error["from_state"] == "brainstorm"
    assert error["legal_edges"] == ["plan", "blocked", "cancelled"]


def test_unknown_token_is_still_unknown_state(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    out = runner.invoke(main, ["transition", "HATS-001", "shipping", *_args(tmp_path), "--json"])
    assert out.exit_code == 1
    assert json.loads(out.stdout)["error"]["code"] == "unknown_state"


# ----- load-time collision: an edge name that equals a state name -------------


def test_edge_name_colliding_with_a_state_is_a_typed_load_error(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: x\nprefix: X\n"
        "fsm:\n"
        "  initial: a\n"
        "  states: [{name: a}, {name: b}]\n"
        "  edges:\n"
        "    - {from: a, to: b, name: a}\n"  # 'a' collides with a state
        "links:\n"
        "  kinds:\n"
        "    - {name: parent_task, arity: one}\n"
    )
    with pytest.raises(EdgeNameStateCollisionError):
        load_backlog(doc)


# ----- parse_ops: --set / --append map onto FieldsOp (Set/Append) -------------


def test_set_and_append_parse_to_field_ops_preserving_argv_order():
    parsed = parse_ops(["--set", "priority=high", "--state", "execute", "--append", 'tags=["x"]'])
    assert [type(o).__name__ for o in parsed] == ["FieldsOp", "StateOp", "FieldsOp"]
    assert parsed[0] == FieldsOp({"priority": Set("high")})
    assert parsed[1] == StateOp("execute")
    # HATS-1299: a JSON array adds its ENTRIES. It used to nest the array as one
    # entry — Append(["x"]) — which no reader could load back.
    assert parsed[2] == FieldsOp({"tags": Append("x")})


def test_set_int_field_coerces_via_field_types():
    parsed = parse_ops(["--set", "budget=5"], field_types={"budget": "int"})
    assert parsed == [FieldsOp({"budget": Set(5)})]  # int, not "5"
    # a str field (or no field_types) stays a string
    assert parse_ops(["--set", "budget=5"]) == [FieldsOp({"budget": Set("5")})]


def test_malformed_set_append_are_typed_op_parse_errors():
    with pytest.raises(OpParseError):
        parse_ops(["--set", "noequals"])
    with pytest.raises(OpParseError):
        parse_ops(["--set", "budget=x"], field_types={"budget": "int"})


# ----- --append/--set payload grammar (HATS-1299) ------------------------------
# JSON when it parses, plain string otherwise; a list field takes an array
# element-wise (--append) or wholesale (--set).


def test_append_bare_text_is_a_string_not_a_json_refusal():
    assert parse_ops(["--append", "tags=delegate-ok"]) == [
        FieldsOp({"tags": Append("delegate-ok")})
    ]
    # the JSON-quoted form keeps working — same result, no second grammar
    assert parse_ops(["--append", 'tags="delegate-ok"']) == [
        FieldsOp({"tags": Append("delegate-ok")})
    ]


def test_append_array_becomes_one_op_per_entry_in_order():
    assert parse_ops(["--append", 'tags=["a","b"]']) == [
        FieldsOp({"tags": Append("a")}),
        FieldsOp({"tags": Append("b")}),
    ]


def test_append_empty_array_is_a_no_op():
    assert parse_ops(["--append", "tags=[]"]) == []


def test_append_onto_a_declared_non_list_field_is_typed():
    # Without this the bare-string fallback would DOWNGRADE today's error: an
    # "invalid JSON" refusal at parse would become an internal marker deeper in.
    with pytest.raises(OpParseError, match="not a list"):
        parse_ops(["--append", "priority=high"], field_types={"priority": "str"})
    # anchor fields are typed by the model, not the backlog schema — same refusal
    with pytest.raises(OpParseError, match="not a list"):
        parse_ops(["--append", "title=Renamed"])


def test_set_replaces_a_list_field_with_a_json_array():
    assert parse_ops(["--set", 'tags=["a","b"]'], field_types={"tags": "list"}) == [
        FieldsOp({"tags": Set(["a", "b"])})
    ]


def test_set_non_array_payload_on_a_list_field_names_the_form():
    with pytest.raises(OpParseError, match="JSON array"):
        parse_ops(["--set", "tags=solo"], field_types={"tags": "list"})


def test_set_on_a_str_field_never_parses_json():
    # A str field takes the payload verbatim — a description that happens to look
    # like JSON must not silently become a list (or an int, or null).
    assert parse_ops(["--set", 'description=["a"]'], field_types={"description": "str"}) == [
        FieldsOp({"description": Set('["a"]')})
    ]
    assert parse_ops(["--set", "title=42"]) == [FieldsOp({"title": Set("42")})]


def test_set_append_refuse_structural_fields():
    # HATS-1067 guard: --set/--append must not bypass the FSM (state) / graph
    # (links) / audit (work_log) verbs, nor touch kernel-owned identity/timestamps.
    for bad in (
        "state=done",
        "parent_task=T-9",
        "depends_on=T-9",
        "related=T-9",
        "links=x",
        "work_log=x",
        "id=T-2",
        "created=now",
        "updated=now",
    ):
        with pytest.raises(OpParseError):
            parse_ops(["--set", bad])
    with pytest.raises(OpParseError):  # --append is guarded the same way
        parse_ops(["--append", 'depends_on=["T-9"]'])


def test_set_still_allows_data_and_plain_anchor_fields():
    # data fields + plain str anchors (title) stay settable — only structural
    # anchors are refused.
    assert parse_ops(["--set", "work_policy=respect API"]) == [
        FieldsOp({"work_policy": Set("respect API")})
    ]
    assert parse_ops(["--set", "title=Renamed"]) == [FieldsOp({"title": Set("Renamed")})]


# ----- exhaustiveness (additive): new flags ride the existing "fields" kind ----


def test_new_flags_are_registered_and_map_to_a_rendered_op_kind():
    assert {"--set", "--append"} <= ops._OP_FLAGS
    # FieldsOp emits the "fields" op kind, which already has a renderer — so the
    # error-surface exhaustiveness pin (_OP_RENDERERS == OP_KINDS) stays whole.
    assert "fields" in ops.OP_KINDS
    assert "fields" in cli._OP_RENDERERS


# ----- CLI vertical slice: schema validation + int coercion end-to-end --------


def test_set_writes_a_declared_field_and_bad_choice_is_typed(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    ok = runner.invoke(
        main, ["transition", "HATS-001", "--set", "priority=high", *_args(tmp_path), "--json"]
    )
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.stdout)
    assert payload["task"]["priority"] == "high"
    assert [o["op"] for o in payload["ops"]] == ["fields"]

    bad = runner.invoke(
        main, ["transition", "HATS-001", "--set", "priority=urgent", *_args(tmp_path), "--json"]
    )
    assert bad.exit_code == 1
    error = json.loads(bad.stdout)["error"]
    assert error["code"] == "invalid_field" and error["field"] == "priority"


def test_append_writes_a_list_field(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    out = runner.invoke(
        main, ["transition", "HATS-001", "--append", 'tags="urgent"', *_args(tmp_path), "--json"]
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["task"]["tags"] == ["urgent"]


def test_the_array_form_no_longer_strands_the_card(tmp_path):
    # HATS-1299 regression, verbatim: this exact call used to write
    # tags: [alpha, [delegate-ok]] and every later verb answered "not found".
    runner = CliRunner()
    _create(runner, tmp_path)
    runner.invoke(main, ["transition", "HATS-001", "--append", "tags=alpha", *_args(tmp_path)])

    out = runner.invoke(
        main,
        ["transition", "HATS-001", "--append", 'tags=["delegate-ok"]', *_args(tmp_path), "--json"],
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["task"]["tags"] == ["alpha", "delegate-ok"]

    # the card is still addressable — the half of the defect that hurt most
    read = runner.invoke(main, ["context", "HATS-001", *_args(tmp_path), "--json"])
    assert read.exit_code == 0, read.output
    assert json.loads(read.stdout)["task"]["tags"] == ["alpha", "delegate-ok"]


def test_a_card_already_broken_on_disk_is_repairable_through_the_cli(tmp_path):
    # The escape hatch: cards stranded before this fix (or by a foreign writer)
    # must not need a hand-edited task.yaml, which rule_backlog_discipline forbids.
    runner = CliRunner()
    _create(runner, tmp_path)
    card = tmp_path / "tasks" / "HATS-001" / "task.yaml"
    card.write_text(card.read_text() + "tags:\n- alpha\n- - delegate-ok\n", encoding="utf-8")

    repair = runner.invoke(
        main, ["transition", "HATS-001", "--set", 'tags=["alpha"]', *_args(tmp_path), "--json"]
    )

    assert repair.exit_code == 0, repair.output
    assert json.loads(repair.stdout)["task"]["tags"] == ["alpha"]


def test_set_int_field_coerces_end_to_end_over_a_custom_catalog(tmp_path):
    catalog = tmp_path / "tasks"
    catalog.mkdir(parents=True)
    (catalog / "backlog.yaml").write_text(_INT_BACKLOG, encoding="utf-8")
    runner = CliRunner()
    runner.invoke(main, ["create", "c", *_args(tmp_path), "--json"])
    out = runner.invoke(
        main, ["transition", "HATS-001", "--set", "budget=5", *_args(tmp_path), "--json"]
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["task"]["budget"] == 5  # coerced to int, not "5"

    bad = runner.invoke(
        main, ["transition", "HATS-001", "--set", "budget=lots", *_args(tmp_path), "--json"]
    )
    assert bad.exit_code == 1
    assert json.loads(bad.stdout)["error"]["code"] == "invalid_ops"
