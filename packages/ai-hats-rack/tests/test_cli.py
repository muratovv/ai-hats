"""``rack`` CLI: JSON-first verbs + self-documenting FSM refusals."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _tasks_args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def _create(runner, tmp_path, *extra):
    return runner.invoke(main, ["create", "demo task", *extra, *_tasks_args(tmp_path), "--json"])


def test_create_json(runner, tmp_path):
    result = _create(runner, tmp_path)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["id"] == "HATS-001"
    assert payload["task"]["state"] == "brainstorm"
    assert payload["transitions"] == []
    assert payload["journal"] == []


def test_context_json_and_plain(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["task"]["title"] == "demo task"

    plain = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path)])
    assert plain.exit_code == 0
    assert "HATS-001" in plain.output
    assert "brainstorm" in plain.output


def test_cli_surface_is_exactly_create_ls_context_transition(runner, tmp_path):
    # HATS-1031 Р11/Р12: one path per action — show folded into context, log
    # into `transition --log`; the group must not know the dead verbs at all.
    assert set(main.commands) == {"create", "ls", "context", "transition"}
    for verb in ("show", "log"):
        result = runner.invoke(main, [verb, "HATS-001", *_tasks_args(tmp_path)])
        assert result.exit_code == 2
        assert "No such command" in result.output


def test_transition_json_carries_deltas_and_journal(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-001", "plan", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["state"] == "plan"
    assert payload["transitions"] == [
        {"task_id": "HATS-001", "from": "brainstorm", "to": "plan", "reason": ""}
    ]
    assert payload["journal"][0]["event"] == "edge:brainstorm--plan"


def test_invalid_transition_prints_legal_edges(runner, tmp_path):
    _create(runner, tmp_path)
    plain = runner.invoke(main, ["transition", "HATS-001", "done", *_tasks_args(tmp_path)])
    assert plain.exit_code == 1
    # self-documenting refusal (PROP-061): the legal edges from fsm.yaml
    assert "Legal edges from 'brainstorm'" in plain.output
    assert "plan" in plain.output and "blocked" in plain.output and "cancelled" in plain.output

    as_json = runner.invoke(
        main, ["transition", "HATS-001", "done", *_tasks_args(tmp_path), "--json"]
    )
    assert as_json.exit_code == 1
    error = json.loads(as_json.output)["error"]
    assert error["code"] == "invalid_transition"
    assert error["legal_edges"] == ["plan", "blocked", "cancelled"]


def test_unknown_state_lists_known(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-001", "shipping", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_state"
    assert "brainstorm" in error["known_states"]


def test_force_without_reason_is_actionable(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-001", "review", "--force", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "reason" in error["message"]


def test_force_with_reason_relaxes_arrow(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(
        main,
        [
            "transition",
            "HATS-001",
            "review",
            "--force",
            "--reason",
            "test override",
            *_tasks_args(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task"]["state"] == "review"


def test_log_op_appends_work_log(runner, tmp_path):
    # `rack log` died (HATS-1031 Р12) — the work-log op rides the composite.
    _create(runner, tmp_path)
    result = runner.invoke(
        main,
        ["transition", "HATS-001", "--log", "made progress", *_tasks_args(tmp_path), "--json"],
    )
    assert result.exit_code == 0
    entries = json.loads(result.output)["task"]["work_log"]
    assert any("made progress" in e["message"] for e in entries)


def test_unknown_task_exits_nonzero(runner, tmp_path):
    result = runner.invoke(main, ["context", "HATS-404", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


def test_create_with_parent_reports_epicify_journal(runner, tmp_path):
    _create(runner, tmp_path)
    result = runner.invoke(
        main,
        ["create", "child", "--parent", "HATS-001", *_tasks_args(tmp_path), "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task"]["parent_task"] == "HATS-001"
    assert [r["event"] for r in payload["journal"]] == ["epicify"]
