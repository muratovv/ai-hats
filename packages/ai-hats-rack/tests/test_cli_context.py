"""``rack tree/link/unlink/context/ls`` CLI verbs (HATS-1024, K5).

Includes the F4-replica metric pin: the discovery context of a task inside an
epic with five design attachments must stay orders of magnitude below the
209 851-char content-injection baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.models import TaskCard


@pytest.fixture
def runner():
    return CliRunner()


def _args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def make_card(tmp_path, task_id, **fields):
    card = TaskCard(id=task_id, **fields)
    path = tmp_path / "tasks" / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)
    return path.parent


def _family(tmp_path):
    epic_dir = make_card(tmp_path, "HATS-1", title="the epic", state="execute", priority="high")
    (epic_dir / "plan.md").write_text("# epic plan body")
    task_dir = make_card(
        tmp_path,
        "HATS-2",
        title="the task",
        state="plan",
        description="do the thing",
        parent_task="HATS-1",
        depends_on=["HATS-3"],
        related=["HATS-4"],
    )
    (task_dir / "notes.md").write_text("notes body")
    dep_dir = make_card(tmp_path, "HATS-3", title="dep", state="done", resolution="merged")
    (dep_dir / "summary.md").write_text("dep summary body")
    make_card(tmp_path, "HATS-4", title="rel", state="execute")
    make_card(tmp_path, "HATS-5", title="kid", state="plan", parent_task="HATS-2")


# ----- tree ---------------------------------------------------------------------


def test_tree_human_two_levels(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["tree", "HATS-1", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "HATS-1 [execute/high] the epic"
    assert lines[1] == "└─ HATS-2 [plan/medium] the task"
    assert lines[2] == "   └─ HATS-5 [plan/medium] kid"


def test_tree_json_is_nested(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["tree", "HATS-1", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    tree = json.loads(result.output)["tree"]
    assert tree["id"] == "HATS-1" and tree["state"] == "execute"
    assert tree["children"][0]["id"] == "HATS-2"
    assert tree["children"][0]["children"][0]["id"] == "HATS-5"


def test_tree_leaf_notes_no_children(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["tree", "HATS-4", *_args(tmp_path)])
    assert result.exit_code == 0
    assert "(no children)" in result.output


def test_tree_unknown_task_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["tree", "HATS-404", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


# ----- link / unlink --------------------------------------------------------------


def test_link_human_and_worklog(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["link", "HATS-4", "HATS-3", "--kind", "depends", *_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Linked: HATS-4 depends_on HATS-3" in result.output
    card = TaskCard.from_yaml(tmp_path / "tasks" / "HATS-4" / "task.yaml")
    assert card.depends_on == ["HATS-3"]
    assert "Linked HATS-3 (depends_on)" in card.work_log[-1].message


def test_link_json_and_idempotent_rerun(runner, tmp_path):
    _family(tmp_path)
    first = runner.invoke(main, ["link", "HATS-4", "HATS-3", *_args(tmp_path), "--json"])
    assert json.loads(first.output) == {
        "task_id": "HATS-4",
        "target": "HATS-3",
        "kinds": ["related"],
        "changed": True,
        "kind": "related",
    }
    second = runner.invoke(main, ["link", "HATS-4", "HATS-3", *_args(tmp_path)])
    assert second.exit_code == 0
    assert "Already linked" in second.output
    card = TaskCard.from_yaml(tmp_path / "tasks" / "HATS-4" / "task.yaml")
    assert len(card.work_log) == 1


def test_link_self_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["link", "HATS-4", "HATS-4", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "self_link"


def test_link_unknown_target_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["link", "HATS-4", "HATS-404", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)["error"]
    assert payload["code"] == "unknown_task" and payload["task_id"] == "HATS-404"


def test_unlink_defaults_to_both_kinds(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["unlink", "HATS-2", "HATS-3", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kinds"] == ["depends_on"] and payload["changed"] is True
    noop = runner.invoke(main, ["unlink", "HATS-2", "HATS-3", *_args(tmp_path)])
    assert noop.exit_code == 0
    assert "Not linked" in noop.output


# ----- context ---------------------------------------------------------------------


def test_context_human_is_discovery_only(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-2", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "the task" in out and "do the thing" in out
    notes = tmp_path / "tasks" / "HATS-2" / "notes.md"
    assert str(notes.absolute()) in out  # documents: absolute path printed
    assert "Parent:" in out and "HATS-1 [execute] the epic" in out
    assert str((tmp_path / "tasks" / "HATS-1" / "plan.md").absolute()) in out
    assert "Depends on:" in out and "resolution: merged" in out
    assert str((tmp_path / "tasks" / "HATS-3" / "summary.md").absolute()) in out
    assert "Related:" in out and "HATS-4 [execute] rel" in out
    assert "Children:" in out and "HATS-5" in out
    assert "tip:" in out
    # discovery, not injection: no document body leaks without --with
    assert "epic plan body" not in out and "dep summary body" not in out


def test_context_json_schema(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-2", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload) == {
        "task",
        "documents",
        "parent",
        "depends_on",
        "related",
        "children",
        "included",
    }
    assert payload["task"]["id"] == "HATS-2"
    assert payload["parent"]["docs"][0]["name"] == "plan.md"
    assert Path(payload["parent"]["docs"][0]["path"]).is_absolute()
    assert payload["parent"]["docs"][0]["mtime"].endswith("Z")
    (dep,) = payload["depends_on"]
    assert dep["resolution"] == "merged"
    assert payload["children"][0]["id"] == "HATS-5"
    assert payload["included"] == []


def test_context_with_embeds_and_marks_truncation(runner, tmp_path):
    _family(tmp_path)
    plan = tmp_path / "tasks" / "HATS-2" / "plan.md"
    plan.write_text("plan " * 200)  # 1000 bytes
    result = runner.invoke(
        main,
        ["context", "HATS-2", "--with", "plan,summary", "--max-bytes", "100", *_args(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "--- HATS-2/plan.md" in result.output
    assert f"[truncated — 1000 bytes on disk; Read the full file: {plan.absolute()}]" in (
        result.output
    )
    assert "--- HATS-3/summary.md" in result.output
    assert "dep summary body" in result.output  # under the cap: embedded whole


def test_context_with_json_truncation_fields(runner, tmp_path):
    _family(tmp_path)
    (tmp_path / "tasks" / "HATS-2" / "plan.md").write_text("x" * 500)
    result = runner.invoke(
        main,
        ["context", "HATS-2", "--with", "plan", "--max-bytes", "64", *_args(tmp_path), "--json"],
    )
    (inc,) = json.loads(result.output)["included"]
    assert inc["truncated"] is True and inc["size"] == 500 and len(inc["content"]) == 64


def test_context_with_missing_summary_is_graceful(runner, tmp_path):
    make_card(tmp_path, "HATS-1", title="lone")
    result = runner.invoke(
        main, ["context", "HATS-1", "--with", "plan,summary", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["included"] == []


def test_context_with_unknown_selector_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-2", "--with", "everything", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_selector"


def test_context_with_unknown_doc_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-2", "--with", "doc:ghost.md", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_document"


def test_context_unknown_task_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-404", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


# ----- ls -------------------------------------------------------------------------


def test_ls_filters_and_json(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["ls", "--state", "plan", "--parent", "HATS-1", *_args(tmp_path), "--json"]
    )
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["tasks"][0]["id"] == "HATS-2"
    grep = runner.invoke(main, ["ls", "--grep", "THING", *_args(tmp_path), "--json"])
    assert [t["id"] for t in json.loads(grep.output)["tasks"]] == ["HATS-2"]


def test_ls_human_table_and_empty(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["ls", *_args(tmp_path)])
    assert result.exit_code == 0
    assert "HATS-1" in result.output and "[execute]" in result.output
    assert "5 task(s)" in result.output
    empty = runner.invoke(main, ["ls", "--tag", "nope", *_args(tmp_path)])
    assert empty.exit_code == 0
    assert "No tasks match." in empty.output


# ----- F4 replica metric ------------------------------------------------------------


def test_f4_replica_context_stays_small(runner, tmp_path):
    """Baseline F4: 209 851 chars over 10 calls. One discovery call on the same
    shape (epic with 5 fat attachments + depends_on + related) must stay <8K."""
    epic_dir = make_card(tmp_path, "HATS-100", title="design epic", state="execute")
    (epic_dir / "plan.md").write_text("# plan\n" + "design detail\n" * 100)
    attachments = epic_dir / "attachments"
    attachments.mkdir()
    for i in range(5):
        (attachments / f"design-doc-{i}.md").write_text(f"# doc {i}\n" + ("lorem ipsum " * 3000))
    for i, dep in enumerate(("HATS-101", "HATS-102", "HATS-103", "HATS-104")):
        dep_dir = make_card(tmp_path, dep, title=f"dep {i}", state="done", resolution="merged")
        (dep_dir / "summary.md").write_text(f"# summary {i}\n" + "outcome line\n" * 50)
    make_card(tmp_path, "HATS-105", title="sibling", state="execute", parent_task="HATS-100")
    make_card(
        tmp_path,
        "HATS-106",
        title="the measured task",
        state="execute",
        description="collect full context",
        parent_task="HATS-100",
        depends_on=["HATS-101", "HATS-102", "HATS-103", "HATS-104"],
        related=["HATS-105"],
    )
    result = runner.invoke(main, ["context", "HATS-106", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "lorem ipsum" not in result.output  # attachment bodies never leak
    assert len(result.output) < 8000, f"discovery package grew to {len(result.output)} chars"
