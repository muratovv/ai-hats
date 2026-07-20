"""``rack plan-extract`` — parity with the tracker's plan-extract semantics
(HATS-1054 R4): heading parsing, child creation, parent linkage, idempotency.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main

_SUBTASKS_PLAN = (
    "# Plan\n\n"
    "## Subtasks\n\n"
    "- Carve out the widget\n"
    "- Wire the seam\n"
)


@pytest.fixture
def runner():
    return CliRunner()


def _args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def _create_parent(runner, tmp_path, plan_text=_SUBTASKS_PLAN) -> str:
    res = runner.invoke(main, ["create", "parent", *_args(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    parent_id = json.loads(res.output)["task"]["id"]
    plan = tmp_path / "tasks" / parent_id / "plan.md"
    plan.write_text(plan_text)
    return parent_id


def _context(runner, tmp_path, task_id):
    res = runner.invoke(main, ["context", task_id, *_args(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


# ----- verb registration -----------------------------------------------------


def test_plan_extract_is_a_top_level_verb():
    assert "plan-extract" in main.commands


# ----- dry-run parses candidates without mutating ----------------------------


def test_dry_run_lists_candidates_and_mutates_nothing(runner, tmp_path):
    parent = _create_parent(runner, tmp_path)
    res = runner.invoke(
        main, ["plan-extract", parent, "--dry-run", *_args(tmp_path), "--json"]
    )
    assert res.exit_code == 0, res.output
    titles = [c["title"] for c in json.loads(res.output)["candidates"]]
    assert titles == ["Carve out the widget", "Wire the seam"]
    # nothing created: parent has no children on disk
    listing = runner.invoke(main, ["ls", *_args(tmp_path), "--json"])
    assert json.loads(listing.output)  # only the parent card exists
    plan = (tmp_path / "tasks" / parent / "plan.md").read_text()
    assert "<!--" not in plan


# ----- real run creates children, parents them, stamps the plan --------------


def test_run_creates_children_parented_and_stamps_plan(runner, tmp_path):
    parent = _create_parent(runner, tmp_path)
    res = runner.invoke(main, ["plan-extract", parent, *_args(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["task_id"] == parent
    created = payload["created"]
    assert [c["title"] for c in created] == ["Carve out the widget", "Wire the seam"]

    # each child card carries parent_task + the extracted-from-plan tag
    for child in created:
        ctx = _context(runner, tmp_path, child["id"])
        assert ctx["task"]["parent_task"] == parent
        assert "extracted-from-plan" in ctx["task"]["tags"]

    # the plan lines are stamped with each child id (idempotency marker)
    plan = (tmp_path / "tasks" / parent / "plan.md").read_text()
    for child in created:
        assert f"<!-- {child['id']} -->" in plan


# ----- idempotency: re-running skips already-stamped lines --------------------


def test_rerun_is_idempotent(runner, tmp_path):
    parent = _create_parent(runner, tmp_path)
    first = runner.invoke(main, ["plan-extract", parent, *_args(tmp_path), "--json"])
    assert len(json.loads(first.output)["created"]) == 2

    second = runner.invoke(main, ["plan-extract", parent, *_args(tmp_path), "--json"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["created"] == []


# ----- steps-checklist and numbered-heading parsing parity -------------------


def test_steps_checklist_parity(runner, tmp_path):
    plan = "# Plan\n\n## Steps\n\n- [ ] First step\n- [x] Second step\n"
    parent = _create_parent(runner, tmp_path, plan)
    res = runner.invoke(
        main, ["plan-extract", parent, "--dry-run", *_args(tmp_path), "--json"]
    )
    cands = json.loads(res.output)["candidates"]
    assert [c["title"] for c in cands] == ["First step", "Second step"]
    assert {c["kind"] for c in cands} == {"steps"}


def test_numbered_heading_parity(runner, tmp_path):
    plan = "# Plan\n\n### 1. Alpha\n\n### 2. Beta\n"
    parent = _create_parent(runner, tmp_path, plan)
    res = runner.invoke(
        main, ["plan-extract", parent, "--dry-run", *_args(tmp_path), "--json"]
    )
    cands = json.loads(res.output)["candidates"]
    assert [c["title"] for c in cands] == ["Alpha", "Beta"]
    assert {c["kind"] for c in cands} == {"phase"}


# ----- typed refusals --------------------------------------------------------


def test_unknown_task_is_typed(runner, tmp_path):
    (tmp_path / "tasks").mkdir()
    res = runner.invoke(main, ["plan-extract", "HATS-999", *_args(tmp_path), "--json"])
    assert res.exit_code == 1
    assert json.loads(res.output)["error"]["code"] == "unknown_task"


def test_missing_plan_is_typed(runner, tmp_path):
    res = runner.invoke(main, ["create", "no plan", *_args(tmp_path), "--json"])
    parent = json.loads(res.output)["task"]["id"]
    out = runner.invoke(main, ["plan-extract", parent, *_args(tmp_path), "--json"])
    assert out.exit_code == 1
    assert json.loads(out.output)["error"]["code"] == "unknown_task"


def test_no_candidates_is_empty_created(runner, tmp_path):
    parent = _create_parent(runner, tmp_path, "# Plan\n\nProse only, no sections.\n")
    out = runner.invoke(main, ["plan-extract", parent, *_args(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["created"] == []
