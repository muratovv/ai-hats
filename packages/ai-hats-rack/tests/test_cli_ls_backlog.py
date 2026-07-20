"""HATS-1080: `rack ls --backlog <name>` / `--all-backlogs` — backlog-as-filter on
the no-id scan.

The no-id scan was tasks-catalog scoped (``scan_cards(root.tasks_dir)``); this gives
it a backlog selector resolved dynamically against the mounted workspace instances
(open registry — any sibling ``backlog.yaml`` under ``tracker/`` resolves), not a
hardcoded ``tasks|hyp|prop`` set.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.definition import packaged_definition_source


@pytest.fixture
def runner():
    return CliRunner()


def _tracker_with_backlogs(tmp_path):
    """Conventional tracker layout: tasks + hypotheses + proposals siblings."""
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    tasks = tracker / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    for name in ("hypotheses", "proposals"):
        d = tracker / name
        d.mkdir(parents=True)
        (d / "backlog.yaml").write_text(packaged_definition_source(name), encoding="utf-8")
    return tasks


def _run(runner, tasks, *args):
    return runner.invoke(
        main, list(args), env={"RACK_TASKS_DIR": str(tasks)}, catch_exceptions=False
    )


def _seed(runner, tasks):
    """One card in each of tasks / hypotheses so a filter can discriminate."""
    assert _run(runner, tasks, "create", "a task").exit_code == 0
    assert (
        _run(runner, tasks, "hyp", "create", "an idea", "--hypothesis", "X causes Y").exit_code == 0
    )


def _seed_all(runner, tasks):
    """One card in each mounted backlog: tasks / hyp / proposal."""
    assert _run(runner, tasks, "create", "a task").exit_code == 0
    assert _run(runner, tasks, "hyp", "create", "an idea", "--hypothesis", "H").exit_code == 0
    assert (
        _run(
            runner, tasks, "proposal", "create", "a prop",
            "--category", "rule", "--target", "t", "--description", "d", "--rationale", "why",
        ).exit_code
        == 0
    )


def test_ls_backlog_hyp_returns_only_hyp_cards(runner, tmp_path):
    tasks = _tracker_with_backlogs(tmp_path)
    _seed(runner, tasks)

    result = _run(runner, tasks, "ls", "--backlog", "hyp", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ids = [row["id"] for row in payload["tasks"]]
    assert ids, "expected the HYP card in the --backlog hyp scan"
    assert all(i.startswith("HYP-") for i in ids), ids


def test_all_backlogs_interleaves_and_tags_backlog(runner, tmp_path):
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    payload = json.loads(_run(runner, tasks, "ls", "--all-backlogs", "--json").output)

    by_backlog = {row["id"].split("-")[0]: row["backlog"] for row in payload["tasks"]}
    assert by_backlog == {"HATS": "tasks", "HYP": "hyp", "PROP": "proposal"}


def test_backlog_selector_json_carries_backlog(runner, tmp_path):
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    payload = json.loads(_run(runner, tasks, "ls", "--backlog", "hyp", "--json").output)

    assert payload["tasks"]
    assert all(row["backlog"] == "hyp" for row in payload["tasks"])


def test_default_ls_json_has_no_backlog_key(runner, tmp_path):
    # R2: bare `rack ls` output is unchanged — no backlog annotation, tasks only.
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    payload = json.loads(_run(runner, tasks, "ls", "--json").output)

    assert payload["tasks"], "expected the task card"
    assert all("backlog" not in row for row in payload["tasks"])
    assert all(row["id"].startswith("HATS-") for row in payload["tasks"])


def test_all_backlogs_human_shows_backlog_column(runner, tmp_path):
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    out = _run(runner, tasks, "ls", "--all-backlogs").output

    assert "hyp" in out and "proposal" in out
    assert "HYP-" in out and "PROP-" in out and "HATS-" in out


def test_unknown_backlog_is_typed_error(runner, tmp_path):
    # R5: a miss names what is mounted — not an empty scan, not a traceback.
    tasks = _tracker_with_backlogs(tmp_path)
    _seed(runner, tasks)

    out = _run(runner, tasks, "ls", "--backlog", "nope", "--json")

    assert out.exit_code == 1
    err = json.loads(out.output)["error"]
    assert err["code"] == "unknown_backlog"
    assert set(err["mounted"]) >= {"tasks", "hyp", "proposal"}


def _minimal_backlog(name: str, prefix: str, cli_alias: str) -> str:
    return (
        f"name: {name}\nprefix: {prefix}\ncli_alias: {cli_alias}\n"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: relates, arity: many}]\n"
    )


def test_custom_backlog_resolves_open_registry(runner, tmp_path):
    # Open registry: a sibling backlog.yaml with its own prefix/alias mounts and
    # resolves via --backlog <alias> — not a hardcoded tasks/hyp/prop set.
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    tasks = tracker / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    widgets = tracker / "widgets"
    widgets.mkdir(parents=True)
    (widgets / "backlog.yaml").write_text(
        _minimal_backlog("widgets", "WID", "wid"), encoding="utf-8"
    )

    out = _run(runner, tasks, "ls", "--backlog", "wid", "--json")

    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["tasks"] == []  # resolved + scanned, just empty


def test_tag_filter_is_read_tolerant_across_backlogs(runner, tmp_path):
    # Q4: a filter on a field some backlog lacks = non-match, never an error.
    tasks = _tracker_with_backlogs(tmp_path)
    assert _run(runner, tasks, "create", "tagged", "--tag", "keep").exit_code == 0
    assert _run(runner, tasks, "hyp", "create", "an idea", "--hypothesis", "H").exit_code == 0

    out = _run(runner, tasks, "ls", "--all-backlogs", "--tag", "keep", "--json")

    assert out.exit_code == 0, out.output  # hyp lacks the tag → excluded, not a crash
    ids = [r["id"] for r in json.loads(out.output)["tasks"]]
    assert ids and all(i.startswith("HATS-") for i in ids)


def test_state_filter_narrows_per_backlog_vocabulary(runner, tmp_path):
    # Each backlog owns its state vocabulary (tasks 'brainstorm', hyp 'active');
    # --state is a predicate that self-narrows to the backlogs that use the value.
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    brains = json.loads(
        _run(runner, tasks, "ls", "--all-backlogs", "--state", "brainstorm", "--json").output
    )
    assert [r["id"].split("-")[0] for r in brains["tasks"]] == ["HATS"]

    active = json.loads(
        _run(runner, tasks, "ls", "--all-backlogs", "--state", "active", "--json").output
    )
    assert [r["id"].split("-")[0] for r in active["tasks"]] == ["HYP"]


def test_all_backlogs_composes_with_cap(runner, tmp_path):
    # The workspace scan flows through the shared cap/--all envelope (HATS-1047).
    tasks = _tracker_with_backlogs(tmp_path)
    _seed_all(runner, tasks)

    payload = json.loads(_run(runner, tasks, "ls", "--all-backlogs", "--json").output)

    assert payload["capped"] is False
    assert payload["count"] == payload["total"] == 3
