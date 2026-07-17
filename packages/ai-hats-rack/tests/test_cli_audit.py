"""``rack audit``: JSON-first query surface, filters, zero-events warning
(HATS-1025; PROP-005/076 detection, stable v1 schema pin)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.journal import ENV_ROOT_PID, ENV_SESSION_ID
from ai_hats_rack.kernel import Kernel


@pytest.fixture(autouse=True)
def _clean_identity_env(monkeypatch):
    monkeypatch.delenv(ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(ENV_ROOT_PID, raising=False)


@pytest.fixture
def runner():
    return CliRunner()


def _tasks_args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


_FILLED_PLAN = (
    "# Plan\n\n## Requirements\nr\n\n## Scope & Out-of-scope\ns\n\n"
    "## Steps\nx\n\n## Verification Protocol\nv\n"
)


def _drive(runner, tmp_path, session="s1"):
    """create HATS-001 and walk brainstorm→plan→execute through the CLI."""
    env = {ENV_SESSION_ID: session}
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)], env=env)
    plan = runner.invoke(main, ["transition", "HATS-001", "plan", *_tasks_args(tmp_path)], env=env)
    assert plan.exit_code == 0, plan.output
    # Fill the scaffolded plan so the (now-wired) plan-gate lets execute through.
    (tmp_path / "tasks" / "HATS-001" / "plan.md").write_text(_FILLED_PLAN)
    execute = runner.invoke(
        main, ["transition", "HATS-001", "execute", *_tasks_args(tmp_path)], env=env
    )
    assert execute.exit_code == 0, execute.output


def test_audit_human_feed(runner, tmp_path):
    _drive(runner, tmp_path)
    result = runner.invoke(main, ["audit", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "edge:brainstorm--plan" in result.output
    assert "[plan → execute]" in result.output
    assert "actor=session:s1" in result.output
    assert "result=persisted" in result.output
    assert "warning:" not in result.output


def test_audit_json_schema_is_stable(runner, tmp_path):
    _drive(runner, tmp_path)
    result = runner.invoke(main, ["audit", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert sorted(payload) == ["records", "task_id", "warnings"]
    assert payload["task_id"] == "HATS-001"
    assert payload["warnings"] == []
    assert len(payload["records"]) == 2
    record = payload["records"][0]
    # v1 record schema pin: key set AND order are the contract.
    assert list(record) == [
        "v",
        "ts",
        "event",
        "task_id",
        "detail",
        "actor",
        "force",
        "reason",
        "result",
        "outcomes",
        "identity",
    ]
    assert record["v"] == 1
    assert record["detail"] == {"from": "brainstorm", "to": "plan"}
    assert record["identity"]["verdict"] == "verified"


def test_audit_filters_narrow_the_feed(runner, tmp_path):
    _drive(runner, tmp_path)

    def records(*extra):
        result = runner.invoke(
            main, ["audit", "HATS-001", *extra, *_tasks_args(tmp_path), "--json"]
        )
        return json.loads(result.output)["records"]

    assert len(records()) == 2
    assert [r["event"] for r in records("--event", "edge:plan--execute")] == ["edge:plan--execute"]
    assert len(records("--actor", "session:s1")) == 2
    assert records("--actor", "session:nobody") == []
    assert records("--since", "9999-01-01T00:00:00Z") == []
    assert len(records("--since", "2000-01-01T00:00:00Z")) == 2


def test_zero_events_warning_when_journal_is_dark(runner, tmp_path):
    # Transitions through a sink-less kernel = the "sink fell off" scenario.
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)])
    Kernel(tmp_path / "tasks").transition(
        "HATS-001", "plan", actor="session:s1", caller_cwd=tmp_path
    )

    human = runner.invoke(main, ["audit", "HATS-001", *_tasks_args(tmp_path)])
    assert human.exit_code == 0
    assert "zero-events" in human.output

    machine = runner.invoke(main, ["audit", "HATS-001", *_tasks_args(tmp_path), "--json"])
    payload = json.loads(machine.output)
    assert payload["records"] == []
    assert any("zero-events" in w for w in payload["warnings"])


def test_no_warning_for_untouched_task(runner, tmp_path):
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)])
    result = runner.invoke(main, ["audit", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0
    assert "(no journal records)" in result.output
    assert "zero-events" not in result.output


def test_audit_unknown_task_is_a_typed_error(runner, tmp_path):
    result = runner.invoke(main, ["audit", "HATS-404", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"
