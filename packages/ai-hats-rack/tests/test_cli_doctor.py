"""``rack doctor`` (HATS-1335): the backlog integrity report as a CLI verb.
Read-only; exit 0 on a clean workspace, exit 1 when findings exist."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.checks import CheckDeclaration
from ai_hats_rack.cli import main
from ai_hats_rack.definition import packaged_definition_source
from ai_hats_rack.dispatch import AbortOperation
from ai_hats_rack.workspace import UnknownBacklogError


@pytest.fixture
def runner():
    return CliRunner()


def _tracker(tmp_path):
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    tasks = tracker / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    d = tracker / "hypotheses"
    d.mkdir(parents=True)
    (d / "backlog.yaml").write_text(packaged_definition_source("hypotheses"), encoding="utf-8")
    return tasks


def _run(runner, tasks, *args):
    return runner.invoke(
        main, list(args), env={"RACK_TASKS_DIR": str(tasks)}, catch_exceptions=False
    )


def _write_card(catalog, task_id, body):
    d = catalog / task_id
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(body, encoding="utf-8")


def test_doctor_clean_workspace_exits_zero(runner, tmp_path):
    tasks = _tracker(tmp_path)
    assert _run(runner, tasks, "create", "a task").exit_code == 0
    result = _run(runner, tasks, "doctor")
    assert result.exit_code == 0
    assert "no findings" in result.output


def test_doctor_reports_findings_and_exits_one(runner, tmp_path):
    tasks = _tracker(tmp_path)
    # The HATS-1096..1104 shape, written raw the way broken data actually lands.
    _write_card(tasks, "HATS-1", "id: HATS-1\ntitle: t\nstate: brainstorm\nparent_task: '1'\n")
    result = _run(runner, tasks, "doctor", "--json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["clean"] is False
    assert [(f["check"], f["task_id"], f["target"]) for f in payload["findings"]] == [
        ("dangling-link", "HATS-1", "1")
    ]
    assert payload["findings"][0]["backlog"] == "tasks"


def test_doctor_human_output_names_check_kind_and_target(runner, tmp_path):
    tasks = _tracker(tmp_path)
    _write_card(tasks, "HATS-1", "id: HATS-1\ntitle: t\nstate: brainstorm\nparent_task: '1'\n")
    result = _run(runner, tasks, "doctor")
    assert result.exit_code == 1
    assert "dangling-link" in result.output
    assert "HATS-1" in result.output
    assert "parent_task" in result.output


# ----- the binding section (HATS-1584) ---------------------------------------


class _Port:
    """A check port stub: the rows an integrator would have resolved."""

    def __init__(self, *rows, error: Exception | None = None) -> None:
        self._rows = rows
        self._error = error

    def check_declarations(self):
        if self._error is not None:
            raise self._error
        return self._rows


class _Provider:
    """Integrator wiring reduced to the half the doctor reads."""

    def __init__(self, port) -> None:
        self._port = port

    def check_port(self, root, catalog):
        return self._port


def _binding(point, *, backlog="tasks"):
    return CheckDeclaration(
        path=(backlog,),
        at=(point,),
        cargo={},
        on_error="refuse",
        label="'role' binds skill/gate.sh under apps.rack",
        handle=None,
    )


def _with_provider(monkeypatch, provider):
    monkeypatch.setattr("ai_hats_rack.verbs.doctor._provider", lambda: provider)


def test_a_dead_point_is_a_finding_and_reddens_the_report(runner, tmp_path, monkeypatch):
    """The compensation ADR-0019 owes: a typo in an `edge:` point is silent on
    every transition, so the doctor is where it is said out loud."""
    tasks = _tracker(tmp_path)
    _with_provider(monkeypatch, _Provider(_Port(_binding("edge:reviw--done"))))

    result = _run(runner, tasks, "doctor", "--json")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["clean"] is False
    assert [f["check"] for f in payload["findings"]] == ["dead-check-point"]
    assert [(r["status"], r["point"]) for r in payload["bindings"]["rows"]] == [
        ("dead", "edge:reviw--done")
    ]


def test_a_sibling_backlogs_point_is_reported_but_not_a_finding(runner, tmp_path, monkeypatch):
    """HATS-1545 R10 made the cross-backlog skip legal, and it stays legal: the
    row is listed as foreign, and the report is still clean."""
    tasks = _tracker(tmp_path)
    _with_provider(monkeypatch, _Provider(_Port(_binding("edge:active--confirmed"))))

    result = _run(runner, tasks, "doctor", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["findings"] == []
    assert [r["status"] for r in payload["bindings"]["rows"]] == ["foreign"]


def test_a_row_naming_no_mounted_backlog_is_a_finding(runner, tmp_path, monkeypatch):
    """The refusal a transition would raise, said before the transition."""
    tasks = _tracker(tmp_path)
    _with_provider(monkeypatch, _Provider(_Port(_binding("edge:review--done", backlog="cards"))))

    result = _run(runner, tasks, "doctor", "--json")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert [f["check"] for f in payload["findings"]] == ["unaddressable-check-row"]
    assert "cli_alias" in payload["findings"][0]["detail"]


def test_rows_that_cannot_be_read_are_a_finding_not_a_traceback(runner, tmp_path, monkeypatch):
    """A role whose row is broken is the state this section exists to report, so
    the failure to resolve one must not take the whole report down with it."""
    tasks = _tracker(tmp_path)
    boom = AbortOperation("checks: script not found at /nope/gate.sh")
    _with_provider(monkeypatch, _Provider(_Port(error=boom)))

    result = _run(runner, tasks, "doctor", "--json")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert [f["check"] for f in payload["findings"]] == ["unreadable-check-rows"]
    assert "/nope/gate.sh" in payload["findings"][0]["detail"]


def test_an_integrator_without_a_check_port_says_so_and_stays_clean(runner, tmp_path, monkeypatch):
    """A bare rack has nothing to declare a gate with (ADR-0019 D11 clause 5) —
    that is not a defect, but it is not an armed gate either, so it is said."""
    tasks = _tracker(tmp_path)
    _with_provider(monkeypatch, object())

    result = _run(runner, tasks, "doctor", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["bindings"]["rows"] == []
    assert "no integrator supplies a check executor" in payload["bindings"]["note"]


def test_the_json_shape_tells_a_finished_run_from_one_that_could_not_start(
    runner, tmp_path, monkeypatch
):
    """I2 (HATS-1546): findings and typed failure share exit 1 — every typed
    refusal in this package leaves through one `fail()` with a literal 1 — so
    the discriminator is the SHAPE, and a caller branches on the `error` key.
    """
    tasks = _tracker(tmp_path)
    _with_provider(monkeypatch, _Provider(_Port(_binding("edge:review--done"))))
    finished = json.loads(_run(runner, tasks, "doctor", "--json").output)

    monkeypatch.setattr(
        "ai_hats_rack.verbs.doctor.diagnose_workspace",
        lambda _ws: (_ for _ in ()).throw(UnknownBacklogError("nope", ("tasks",))),
    )
    failed_run = _run(runner, tasks, "doctor", "--json")

    assert set(finished) == {"clean", "scanned", "findings", "bindings"}
    assert failed_run.exit_code == 1
    assert set(json.loads(failed_run.output)) == {"error"}
