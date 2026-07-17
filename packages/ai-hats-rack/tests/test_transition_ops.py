"""Ordered composite transition (HATS-1030): argv-order parse, staged overlay
visibility, abort rollback (files + card), revert-info, links, journal — kernel
and CLI vertical slices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.dispatch import OperationAborted
from ai_hats_rack.docstore import UnknownDocumentError
from ai_hats_rack.extensions import standalone_extensions
from ai_hats_rack.kernel import UnknownTaskError
from ai_hats_rack.ops import (
    AttachOp,
    LinkOp,
    OpParseError,
    StateOp,
    UnlinkOp,
    parse_ops,
)
from ai_hats_rack.registry import DerivedLinkKindError, UnknownLinkKindError
from rack_testkit import CollectingSink, make_kernel

FILLED_PLAN = (
    "# Plan\n\n## Requirements\nr\n\n## Scope & Out-of-scope\ns\n\n"
    "## Steps\nx\n\n## Verification Protocol\nv\n"
)


def _gated_kernel(tasks_dir, **kw):
    return make_kernel(tasks_dir, subscribers=standalone_extensions(tasks_dir), **kw)


def _in_plan(kernel, tasks_dir, cwd, tid="T-1"):
    kernel.create(actor="t", caller_cwd=cwd, task_id=tid, title="probe")
    kernel.transition(tid, "plan", actor="t", caller_cwd=cwd)  # scaffolds empty plan.md
    return tasks_dir / tid


# ----- parse_ops: argv order = execution order --------------------------------


def test_parse_ops_preserves_argv_order():
    a = parse_ops(["--attach", "p.md", "--state", "execute"])
    b = parse_ops(["--state", "execute", "--attach", "p.md"])
    assert [type(o).__name__ for o in a] == ["AttachOp", "StateOp"]
    assert [type(o).__name__ for o in b] == ["StateOp", "AttachOp"]
    assert a != b  # permuting the flags permutes the op list


def test_parse_ops_bare_state_is_old_form_sugar():
    assert parse_ops(["execute"]) == [StateOp("execute")]


def test_parse_ops_attach_splits_src_and_name():
    assert parse_ops(["--attach", "src/draft.md:plan.md"]) == [AttachOp("src/draft.md", "plan.md")]
    assert parse_ops(["--attach", "notes.md"]) == [AttachOp("notes.md", "notes.md")]


def test_parse_ops_edge_splits_kind_and_id():
    assert parse_ops(["--link", "depends:T-2"]) == [LinkOp("depends", "T-2")]
    assert parse_ops(["--link", "T-2"]) == [LinkOp("related", "T-2")]
    assert parse_ops(["--unlink", "related:T-2"]) == [UnlinkOp("related", "T-2")]
    assert parse_ops(["--unlink", "T-2"]) == [UnlinkOp(None, "T-2")]


def test_parse_ops_unknown_token_and_missing_value_raise():
    with pytest.raises(OpParseError):
        parse_ops(["--bogus", "x"])
    with pytest.raises(OpParseError):
        parse_ops(["--state"])


# ----- staged overlay: earlier ops are visible to later handlers --------------


def test_attach_before_state_is_seen_by_the_gate(tasks_dir, cwd, tmp_path):
    k = _gated_kernel(tasks_dir)
    _in_plan(k, tasks_dir, cwd)
    ready = tmp_path / "ready.md"
    ready.write_text(FILLED_PLAN)
    ops = parse_ops(["--attach", f"{ready}:plan.md", "--state", "execute"])
    res = k.transition_ops("T-1", ops, actor="t", caller_cwd=cwd)
    assert res.task.state == "execute"
    assert [o["op"] for o in res.ops] == ["attach", "state"]


def test_state_before_attach_is_gated_and_rolls_back(tasks_dir, cwd, tmp_path):
    k = _gated_kernel(tasks_dir)
    card_dir = _in_plan(k, tasks_dir, cwd)
    before = (card_dir / "task.yaml").read_bytes()
    scaffold = (card_dir / "plan.md").read_text()
    ready = tmp_path / "ready.md"
    ready.write_text(FILLED_PLAN)
    ops = parse_ops(["--state", "execute", "--attach", f"{ready}:plan.md"])
    with pytest.raises(OperationAborted):
        k.transition_ops("T-1", ops, actor="t", caller_cwd=cwd)
    assert (card_dir / "task.yaml").read_bytes() == before  # single persist never ran
    assert (card_dir / "plan.md").read_text() == scaffold  # empty scaffold intact


def test_attach_then_freeze_digests_the_materialized_file(tasks_dir, cwd, tmp_path):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    src = tmp_path / "e.log"
    src.write_text("v1")
    res = k.transition_ops(
        "T-1", parse_ops(["--attach", f"{src}:e.log", "--freeze", "e.log"]), actor="t", caller_cwd=cwd
    )
    assert [o["op"] for o in res.ops] == ["attach", "freeze"]
    assert res.ops[1]["digest"].startswith("sha256:")


def test_freeze_before_attach_aborts_and_unwinds(tasks_dir, cwd, tmp_path):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    src = tmp_path / "e.log"
    src.write_text("v1")
    with pytest.raises(UnknownDocumentError):
        k.transition_ops(
            "T-1", parse_ops(["--freeze", "e.log", "--attach", f"{src}:e.log"]),
            actor="t", caller_cwd=cwd,
        )
    assert not (tasks_dir / "T-1" / "e.log").exists()  # the later attach was rolled back


# ----- abort rolls back the whole sequence — files included -------------------


def test_abort_after_attach_removes_staged_file_and_leaves_card_untouched(tasks_dir, cwd, tmp_path):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    card_dir = tasks_dir / "T-1"
    before = (card_dir / "task.yaml").read_bytes()
    src = tmp_path / "a.md"
    src.write_text("body")
    with pytest.raises(UnknownDocumentError):
        k.transition_ops(
            "T-1", parse_ops(["--attach", f"{src}:a.md", "--rm", "ghost.md"]),
            actor="t", caller_cwd=cwd,
        )
    assert not (card_dir / "a.md").exists()  # zero staged residue
    assert (card_dir / "task.yaml").read_bytes() == before  # zero-byte card change


def test_abort_after_rm_moves_the_trashed_file_back(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    card_dir = tasks_dir / "T-1"
    (card_dir / "doc.md").write_text("keep")
    with pytest.raises(UnknownDocumentError):
        k.transition_ops(
            "T-1", parse_ops(["--rm", "doc.md", "--freeze", "ghost.md"]), actor="t", caller_cwd=cwd
        )
    assert (card_dir / "doc.md").read_text() == "keep"  # restored from trash on abort


# ----- revert-info on destructive ops -----------------------------------------


def test_rm_carries_trash_path_and_ready_revert_command(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    card_dir = tasks_dir / "T-1"
    (card_dir / "doc.md").write_text("keep")
    res = k.transition_ops("T-1", parse_ops(["--rm", "doc.md"]), actor="t", caller_cwd=cwd)
    (op,) = res.ops
    assert op["op"] == "rm" and Path(op["trashed_to"]).is_file()  # recoverable, not deleted
    assert op["revert"] == f"rack transition T-1 --attach {op['trashed_to']}:doc.md"
    assert not (card_dir / "doc.md").exists()


def test_unlink_carries_ready_revert_command(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a", depends_on=["T-2"])
    res = k.transition_ops("T-1", parse_ops(["--unlink", "depends:T-2"]), actor="t", caller_cwd=cwd)
    (op,) = res.ops
    assert op["changed"] and op["kinds"] == ["depends_on"]
    assert op["revert"] == "rack transition T-1 --link depends_on:T-2"


# ----- links: configured kind, idempotence, typed refusals --------------------


def test_link_op_configured_kind_then_idempotent(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    k.create(actor="t", caller_cwd=cwd, task_id="T-2", title="b")
    res = k.transition_ops("T-1", parse_ops(["--link", "depends:T-2"]), actor="t", caller_cwd=cwd)
    assert res.ops[0] == {"op": "link", "kind": "depends_on", "target": "T-2", "changed": True}
    again = k.transition_ops("T-1", parse_ops(["--link", "depends:T-2"]), actor="t", caller_cwd=cwd)
    assert again.ops[0]["changed"] is False


def test_link_derived_and_unknown_kinds_are_typed(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    k.create(actor="t", caller_cwd=cwd, task_id="T-2", title="b")
    with pytest.raises(DerivedLinkKindError):
        k.transition_ops("T-1", parse_ops(["--link", "children:T-2"]), actor="t", caller_cwd=cwd)
    with pytest.raises(UnknownLinkKindError):
        k.transition_ops("T-1", parse_ops(["--link", "blocks:T-2"]), actor="t", caller_cwd=cwd)


def test_link_unknown_target_is_typed(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    with pytest.raises(UnknownTaskError):
        k.transition_ops("T-1", parse_ops(["--link", "related:T-404"]), actor="t", caller_cwd=cwd)


# ----- single ops + sugar + empty -------------------------------------------------


def test_single_log_op_is_legal_no_state(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    res = k.transition_ops("T-1", parse_ops(["--log", "hello"]), actor="t", caller_cwd=cwd)
    assert res.transitions == () and res.journal == ()
    assert res.ops == ({"op": "log", "message": "hello"},)
    assert any("hello" in e.message for e in res.task.work_log)


def test_bare_state_sugar_equals_state_op(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    res = k.transition_ops("T-1", parse_ops(["plan"]), actor="t", caller_cwd=cwd)
    assert res.task.state == "plan"
    assert [t.to_state for t in res.transitions] == ["plan"]


def test_empty_ops_refused(tasks_dir, cwd):
    k = make_kernel(tasks_dir)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    with pytest.raises(ValueError):
        k.transition_ops("T-1", [], actor="t", caller_cwd=cwd)


# ----- journal: every op in work_log; edge event stays in the K7 journal ------


def test_journal_carries_every_op_and_the_edge_event(tasks_dir, cwd, tmp_path):
    sink = CollectingSink()
    k = _gated_kernel(tasks_dir, journal_sink=sink)
    _in_plan(k, tasks_dir, cwd)
    ready = tmp_path / "ready.md"
    ready.write_text(FILLED_PLAN)
    ops = parse_ops(["--attach", f"{ready}:plan.md", "--log", "noted", "--state", "execute"])
    res = k.transition_ops("T-1", ops, actor="sess", caller_cwd=cwd)
    msgs = "\n".join(e.message for e in res.task.work_log)
    assert "Attached plan.md (overwrote)" in msgs and "noted" in msgs  # per-op work_log trail
    assert "edge:plan--execute" in [r.event_key for r in sink.records]  # K7 schema unchanged


# ----- CLI vertical slice: order through the real command ---------------------


def test_cli_attach_before_state_vs_reverse(tmp_path):
    runner = CliRunner()
    args = ["--tasks-dir", str(tmp_path / "tasks")]
    ready = tmp_path / "ready.md"
    ready.write_text(FILLED_PLAN)

    runner.invoke(main, ["create", "demo", *args])
    runner.invoke(main, ["transition", "HATS-001", "plan", *args])
    ok = runner.invoke(
        main,
        ["transition", "HATS-001", "--attach", f"{ready}:plan.md", "--state", "execute", *args, "--json"],
    )
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["task"]["state"] == "execute"

    runner.invoke(main, ["create", "demo2", *args])
    runner.invoke(main, ["transition", "HATS-002", "plan", *args])
    bad = runner.invoke(
        main,
        ["transition", "HATS-002", "--state", "execute", "--attach", f"{ready}:plan.md", *args, "--json"],
    )
    assert bad.exit_code == 1
    assert json.loads(bad.output)["error"]["code"] == "aborted"
