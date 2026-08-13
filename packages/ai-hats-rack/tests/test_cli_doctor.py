"""``rack doctor`` (HATS-1335): the backlog integrity report as a CLI verb.
Read-only; exit 0 on a clean workspace, exit 1 when findings exist."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.definition import packaged_definition_source


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


def test_the_json_shape_tells_a_finished_run_from_one_that_could_not_start(runner, tmp_path):
    """I2 (HATS-1546). Findings and a typed failure share exit 1 — every typed
    refusal in this package leaves through one ``fail()`` with a literal 1 — so
    the discriminator is the SHAPE, and the startup script of HATS-1583 branches
    on the ``error`` key rather than on the code.
    """
    tasks = _tracker(tmp_path)

    finished = json.loads(_run(runner, tasks, "doctor", "--json").output)
    with runner.isolated_filesystem():
        could_not_start = runner.invoke(
            main,
            ["doctor", "--json"],
            env={"RACK_TASKS_DIR": "", "AI_HATS_PROJECT_DIR": "", "AI_HATS_DIR": ""},
            catch_exceptions=False,
        )

    assert set(finished) == {"clean", "scanned", "findings", "bindings"}
    assert could_not_start.exit_code == 1
    assert set(json.loads(could_not_start.output)) == {"error"}
