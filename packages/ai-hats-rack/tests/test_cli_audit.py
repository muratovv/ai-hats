"""``rack context --attr audit``: the journal feed read through context now that
the ``audit`` verb is gone (HATS-1029; K7 filters, zero-events, v1 schema pin)."""

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


#: Real id shape (``ai_hats_observe.session``) paired with the root pid its tail
#: names — the identity check reads that pair, not the id alone (ADR-0025 D4).
SESSION = "20260812-101500-3-4242"
ROOT_PID = "4242"


def _session_env(session: str = SESSION) -> dict[str, str]:
    """A session as the wire contract spells it (ADR-0024, ai-hats HATS-1594).

    Written out rather than imported: the rack does not depend on the integrator
    and its tests must not either. That makes this the contract's first
    third-party consumer, which is a feature — if the envelope cannot be
    produced without importing ai-hats, it is not a public format.

    The id alone is no longer a session, twice over: an ai-hats gate bound to an
    FSM edge reads the envelope beside it and refuses when only half is there,
    and the journal checks the id against the root pid its tail names.
    """  # comment-length: allow — why this is duplicated, not imported
    return {
        ENV_SESSION_ID: session,
        ENV_ROOT_PID: session.rsplit("-", 1)[-1],
        "AI_HATS_SESSION_IDENTITY": json.dumps(
            {
                "v": 1,
                "id": session,
                "role": "assistant",
                "provider": "claude",
                "project_dir": "/nonexistent-rack-test-project",
                "session_dir": f"/nonexistent-rack-test-project/.agent/{session}",
                "skills_root": "",
            }
        ),
    }


def _drive(runner, tmp_path, session=SESSION):
    """create HATS-001 and walk brainstorm→plan→execute through the CLI."""
    env = _session_env(session)
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)], env=env)
    plan = runner.invoke(main, ["transition", "HATS-001", "plan", *_tasks_args(tmp_path)], env=env)
    assert plan.exit_code == 0, plan.output
    # Fill the scaffolded plan so the (now-wired) plan-gate lets execute through.
    (tmp_path / "tasks" / "HATS-001" / "plan.md").write_text(_FILLED_PLAN)
    execute = runner.invoke(
        main, ["transition", "HATS-001", "execute", *_tasks_args(tmp_path)], env=env
    )
    assert execute.exit_code == 0, execute.output


def _audit(runner, tmp_path, *extra, as_json=False):
    argv = ["context", "HATS-001", "--attr", "audit", *extra, *_tasks_args(tmp_path)]
    if as_json:
        argv.append("--json")
    return runner.invoke(main, argv)


def test_attr_audit_human_feed(runner, tmp_path):
    _drive(runner, tmp_path)
    result = _audit(runner, tmp_path)
    assert result.exit_code == 0, result.output
    assert "audit:" in result.output
    assert "edge:brainstorm--plan" in result.output
    assert "[plan → execute]" in result.output
    assert f"actor=session:{SESSION}" in result.output
    assert "result=persisted" in result.output
    assert "warning:" not in result.output


def test_attr_audit_json_schema_is_stable(runner, tmp_path):
    _drive(runner, tmp_path)
    result = _audit(runner, tmp_path, as_json=True)
    assert result.exit_code == 0, result.output
    audit = json.loads(result.output)["attrs"]["audit"]
    assert sorted(audit) == ["records", "warnings"]
    assert audit["warnings"] == []
    assert len(audit["records"]) == 2
    record = audit["records"][0]
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


def test_forged_session_pin_is_caught_on_the_cli_road(runner, tmp_path):
    # `cli_common.actor` derives the claim from AI_HATS_SESSION_ID, so the old
    # comparison against that variable could never fail here (ADR-0025 D4).
    env = {**_session_env(SESSION), ENV_ROOT_PID: "9999"}
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)], env=env)
    plan = runner.invoke(main, ["transition", "HATS-001", "plan", *_tasks_args(tmp_path)], env=env)
    assert plan.exit_code == 0, plan.output

    audit = json.loads(_audit(runner, tmp_path, as_json=True).output)["attrs"]["audit"]
    assert audit["records"][0]["identity"]["verdict"] == "mismatch"


def test_attr_audit_filters_narrow_the_feed(runner, tmp_path):
    _drive(runner, tmp_path)

    def records(*extra):
        result = _audit(runner, tmp_path, *extra, as_json=True)
        return json.loads(result.output)["attrs"]["audit"]["records"]

    assert len(records()) == 2
    assert [r["event"] for r in records("--event", "edge:plan--execute")] == ["edge:plan--execute"]
    assert len(records("--actor", f"session:{SESSION}")) == 2
    assert records("--actor", "session:nobody") == []
    assert records("--since", "9999-01-01T00:00:00Z") == []
    assert len(records("--since", "2000-01-01T00:00:00Z")) == 2


def test_zero_events_warning_when_journal_is_dark(runner, tmp_path):
    # Transitions through a sink-less kernel = the "sink fell off" scenario.
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)])
    Kernel(tmp_path / "tasks").transition(
        "HATS-001", "plan", actor="session:s1", caller_cwd=tmp_path
    )

    human = _audit(runner, tmp_path)
    assert human.exit_code == 0
    assert "zero-events" in human.output

    machine = _audit(runner, tmp_path, as_json=True)
    audit = json.loads(machine.output)["attrs"]["audit"]
    assert audit["records"] == []
    assert any("zero-events" in w for w in audit["warnings"])


def test_no_warning_for_untouched_task(runner, tmp_path):
    runner.invoke(main, ["create", "demo", *_tasks_args(tmp_path)])
    result = _audit(runner, tmp_path)
    assert result.exit_code == 0
    assert "(no journal records)" in result.output
    assert "zero-events" not in result.output


def test_attr_audit_unknown_task_is_a_typed_error(runner, tmp_path):
    result = runner.invoke(
        main, ["context", "HATS-404", "--attr", "audit", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


def test_non_state_ops_journaled_and_parent_epicify(runner, tmp_path):
    from ai_hats_rack.journal import read_journal

    # Create epic HATS-001 and child HATS-002
    runner.invoke(main, ["create", "epic", *_tasks_args(tmp_path)])
    runner.invoke(main, ["create", "child", *_tasks_args(tmp_path)])

    # 1. --set op produces op:set in audit.jsonl
    res_set = runner.invoke(
        main, ["transition", "HATS-002", "--set", "priority=high", *_tasks_args(tmp_path)]
    )
    assert res_set.exit_code == 0, res_set.output
    records_set, _ = read_journal(tmp_path / "tasks", "HATS-002")
    assert any(
        r["event"] == "op:set"
        and r["detail"] == {"field": "priority", "op": "set", "value": "high"}
        for r in records_set
    )

    # 2. --link parent_task:HATS-001 produces link:parent_task on child AND epicify on parent
    res_parent = runner.invoke(
        main, ["transition", "HATS-002", "--link", "parent_task:HATS-001", *_tasks_args(tmp_path)]
    )
    assert res_parent.exit_code == 0, res_parent.output

    child_records, _ = read_journal(tmp_path / "tasks", "HATS-002")
    assert any(
        r["event"] == "link:parent_task"
        and r["detail"] == {"kind": "parent_task", "target": "HATS-001"}
        for r in child_records
    )

    parent_records, _ = read_journal(tmp_path / "tasks", "HATS-001")
    assert any(
        r["event"] == "epicify" and r["detail"] == {"epic": "HATS-001", "child": "HATS-002"}
        for r in parent_records
    )
