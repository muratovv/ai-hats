"""``rack doctor`` (HATS-1335): the backlog integrity report as a CLI verb.
Read-only; exit 0 on a clean workspace, exit 1 when findings exist."""

from __future__ import annotations

import json
from unittest import mock

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


def test_a_row_whose_arrow_is_a_siblings_grammar_is_dead_and_exits_one(runner, tmp_path):
    """HATS-1774, the HATS-1719 repro: a row under ``apps.rack.hyp`` bound to
    ``->done`` names a road hypotheses has no state for. The tasks topology does
    have it, and until this ruling that hit elsewhere read as a legal
    cross-backlog skip — the report stayed clean, rc 0, and the gate fired
    nowhere. It is now a finding, and the detail carries the move-it recipe.
    """
    from ai_hats_rack import cli
    from ai_hats_rack.checks import CheckDeclaration

    row = CheckDeclaration(
        path=("hyp",),
        at=("->done",),
        cargo={},
        on_error="refuse",
        label="'role' binds skill/gate.sh under apps.rack",
        handle=None,
    )

    class _Port:
        def check_declarations(self):
            return (row,)

    class _Provider:
        def build_kernel(self, root, caller_cwd):
            return None

        def after_create(self, root, result):  # pragma: no cover - unused here
            pass

        def check_port(self, root, catalog):
            return _Port()

    class _FakeEP:
        def load(self):
            return _Provider

    tasks = _tracker(tmp_path)
    cli._provider.cache_clear()
    try:
        with mock.patch("importlib.metadata.entry_points", lambda group=None: [_FakeEP()]):
            result = _run(runner, tasks, "doctor", "--json")
    finally:
        cli._provider.cache_clear()

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert [(r["status"], r["backlog"], r["selector"]) for r in payload["bindings"]["rows"]] == [
        ("dead", "hyp", "->done")
    ]
    assert [f["check"] for f in payload["findings"]] == ["dead-check-point"]
    assert "apps.rack.tasks" in payload["findings"][0]["detail"]
