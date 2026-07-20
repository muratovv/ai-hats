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
        runner.invoke(main, ["transition", "HATS-001", state, "--force", "--reason", "w", *_args(tmp_path)])
    out = runner.invoke(main, ["transition", "HATS-001", "reopen", *_args(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["task"]["state"] == "execute"  # reopen: done -> execute
    assert payload["transitions"] == [
        {"task_id": "HATS-001", "from": "done", "to": "execute", "reason": ""}
    ]


def test_named_edge_from_the_wrong_state_is_invalid_transition(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)  # sits in brainstorm; `reopen` starts at done
    out = runner.invoke(main, ["transition", "HATS-001", "reopen", *_args(tmp_path), "--json"])
    assert out.exit_code == 1
    error = json.loads(out.output)["error"]
    assert error["code"] == "invalid_transition"
    assert error["from_state"] == "brainstorm"
    assert error["legal_edges"] == ["plan", "blocked", "cancelled"]


def test_unknown_token_is_still_unknown_state(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    out = runner.invoke(main, ["transition", "HATS-001", "shipping", *_args(tmp_path), "--json"])
    assert out.exit_code == 1
    assert json.loads(out.output)["error"]["code"] == "unknown_state"


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
    assert parsed[2] == FieldsOp({"tags": Append(["x"])})


def test_set_int_field_coerces_via_field_types():
    parsed = parse_ops(["--set", "budget=5"], field_types={"budget": "int"})
    assert parsed == [FieldsOp({"budget": Set(5)})]  # int, not "5"
    # a str field (or no field_types) stays a string
    assert parse_ops(["--set", "budget=5"]) == [FieldsOp({"budget": Set("5")})]


def test_malformed_set_append_are_typed_op_parse_errors():
    with pytest.raises(OpParseError):
        parse_ops(["--set", "noequals"])
    with pytest.raises(OpParseError):
        parse_ops(["--append", "tags=notjson"])
    with pytest.raises(OpParseError):
        parse_ops(["--set", "budget=x"], field_types={"budget": "int"})


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
    ok = runner.invoke(main, ["transition", "HATS-001", "--set", "priority=high", *_args(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.output)
    assert payload["task"]["priority"] == "high"
    assert [o["op"] for o in payload["ops"]] == ["fields"]

    bad = runner.invoke(main, ["transition", "HATS-001", "--set", "priority=urgent", *_args(tmp_path), "--json"])
    assert bad.exit_code == 1
    error = json.loads(bad.output)["error"]
    assert error["code"] == "invalid_field" and error["field"] == "priority"


def test_append_writes_a_list_field(tmp_path):
    runner = CliRunner()
    _create(runner, tmp_path)
    out = runner.invoke(main, ["transition", "HATS-001", "--append", 'tags="urgent"', *_args(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["task"]["tags"] == ["urgent"]


def test_set_int_field_coerces_end_to_end_over_a_custom_catalog(tmp_path):
    catalog = tmp_path / "tasks"
    catalog.mkdir(parents=True)
    (catalog / "backlog.yaml").write_text(_INT_BACKLOG, encoding="utf-8")
    runner = CliRunner()
    runner.invoke(main, ["create", "c", *_args(tmp_path), "--json"])
    out = runner.invoke(main, ["transition", "HATS-001", "--set", "budget=5", *_args(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["task"]["budget"] == 5  # coerced to int, not "5"

    bad = runner.invoke(main, ["transition", "HATS-001", "--set", "budget=lots", *_args(tmp_path), "--json"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["error"]["code"] == "invalid_ops"
