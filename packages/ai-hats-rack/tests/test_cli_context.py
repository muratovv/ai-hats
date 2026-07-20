"""``rack link/unlink/context/ls`` CLI verbs (HATS-1024, K5; read surface v2
HATS-1029: ls --deep/--link graph walk, context --with/--attr, dead verbs).

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


# tree folded into `ls --deep` (HATS-1029); its cases live in the ls section.


# ----- link / unlink (absorbed into `transition --link/--unlink`, HATS-1030) ------


def test_link_human_and_worklog(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-4", "--link", "depends:HATS-3", *_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Linked: HATS-4 depends_on HATS-3" in result.output
    card = TaskCard.from_yaml(tmp_path / "tasks" / "HATS-4" / "task.yaml")
    assert card.depends_on == ["HATS-3"]
    assert "Linked HATS-3 (depends_on)" in card.work_log[-1].message


def test_link_json_and_idempotent_rerun(runner, tmp_path):
    _family(tmp_path)
    first = runner.invoke(
        main, ["transition", "HATS-4", "--link", "HATS-3", *_args(tmp_path), "--json"]
    )
    (op,) = json.loads(first.output)["ops"]
    assert op == {"op": "link", "kind": "related", "target": "HATS-3", "changed": True}
    second = runner.invoke(main, ["transition", "HATS-4", "--link", "HATS-3", *_args(tmp_path)])
    assert second.exit_code == 0
    assert "Already linked" in second.output
    card = TaskCard.from_yaml(tmp_path / "tasks" / "HATS-4" / "task.yaml")
    assert len(card.work_log) == 1


def test_link_self_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-4", "--link", "related:HATS-4", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "self_link"


def test_link_unknown_target_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-4", "--link", "related:HATS-404", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)["error"]
    assert payload["code"] == "unknown_task" and payload["task_id"] == "HATS-404"


def test_unlink_defaults_to_both_kinds(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-2", "--unlink", "HATS-3", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.output)["ops"]
    assert op["kinds"] == ["depends_on"] and op["changed"] is True
    assert op["revert"] == "rack transition HATS-2 --link depends_on:HATS-3"
    noop = runner.invoke(main, ["transition", "HATS-2", "--unlink", "HATS-3", *_args(tmp_path)])
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
    assert "Parent task:" in out and "HATS-1 [execute] the epic" in out
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
    # HATS-1028: one top-level `links` object, not scattered parent/depends/...
    # No `enrichments` key here: parent HATS-1 carries no `work_policy` field,
    # so parent-context delivers nothing (HATS-1064; field-based HATS-1067).
    assert set(payload) == {"task", "documents", "links", "included"}
    assert payload["task"]["id"] == "HATS-2"
    links = payload["links"]
    assert list(links) == ["parent_task", "depends_on", "related", "children"]
    parent = links["parent_task"][0]
    assert parent["docs"][0]["name"] == "plan.md"
    assert Path(parent["docs"][0]["path"]).is_absolute()
    assert parent["docs"][0]["mtime"].endswith("Z")
    assert links["depends_on"][0]["resolution"] == "merged"
    assert links["children"][0]["id"] == "HATS-5"
    assert payload["included"] == []


def test_context_covers_former_show_surface(runner, tmp_path):
    # Р11 parity pin (HATS-1031): everything the killed `show` verb emitted —
    # the FULL card, the top-level links object, the documents block — rides
    # `context` with no flags.
    _family(tmp_path)
    card_path = tmp_path / "tasks" / "HATS-2" / "task.yaml"
    card = TaskCard.from_yaml(card_path)
    card.reviewer = "user"
    card.log_work("first step")
    card.log_work("second step")
    card.save(card_path)

    result = runner.invoke(main, ["context", "HATS-2", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    task = payload["task"]
    assert task == card.to_dict()  # the full card, not a trimmed head
    assert {
        "id",
        "title",
        "state",
        "description",
        "priority",
        "assignee",
        "reviewer",
        "role",
        "parent_task",
        "subtasks",
        "tags",
        "work_log",
        "created",
        "updated",
    } <= set(task)
    assert [e["message"] for e in task["work_log"]] == ["first step", "second step"]
    assert set(payload["links"]) == {"parent_task", "depends_on", "related", "children"}
    (doc,) = payload["documents"]
    assert {"name", "path", "mtime", "frozen", "drift"} <= set(doc)
    assert Path(doc["path"]).is_absolute()

    human = runner.invoke(main, ["context", "HATS-2", *_args(tmp_path)])
    assert human.exit_code == 0, human.output
    assert "reviewer: user" in human.output
    assert "work_log:" in human.output
    assert "first step" in human.output and "second step" in human.output


# ----- context --with (pattern over own + linked docs) ----------------------------


def test_context_with_pattern_embeds_and_marks_truncation(runner, tmp_path):
    _family(tmp_path)
    plan = tmp_path / "tasks" / "HATS-2" / "plan.md"
    plan.write_text("plan " * 200)  # 1000 bytes
    result = runner.invoke(
        main,
        [
            "context",
            "HATS-2",
            "--with",
            "plan*",
            "--with",
            "summary*",
            "--max-bytes",
            "100",
            *_args(tmp_path),
        ],
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
        ["context", "HATS-2", "--with", "plan*", "--max-bytes", "64", *_args(tmp_path), "--json"],
    )
    included = json.loads(result.output)["included"]
    own = next(i for i in included if i["task_id"] == "HATS-2")
    assert own["truncated"] is True and own["size"] == 500 and len(own["content"]) == 64


def test_context_with_no_match_is_graceful(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-2", "--with", "ghost*", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["included"] == []


def test_context_unknown_task_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-404", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


# ----- context --attr work_log (audit is exercised in test_cli_audit.py) ----------


def test_context_attr_work_log_is_full(runner, tmp_path):
    card = TaskCard(id="HATS-1", title="t")
    card.log_work("first")
    card.log_work("second")
    path = tmp_path / "tasks" / "HATS-1" / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)
    result = runner.invoke(
        main, ["context", "HATS-1", "--attr", "work_log", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)["attrs"]["work_log"]
    assert [e["message"] for e in entries] == ["first", "second"]
    # human surface renders the full log, not just the latest entry
    human = runner.invoke(main, ["context", "HATS-1", "--attr", "work_log", *_args(tmp_path)])
    assert "work_log:" in human.output and "first" in human.output and "second" in human.output


def test_context_attr_unknown_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-2", "--attr", "bogus", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    err = json.loads(result.output)["error"]
    assert err["code"] == "unknown_attr" and "work_log" in err["known"]


# ----- context batch: prefetch a viewport in one process (HATS-1074) --------------


def test_context_batch_json_is_byte_identical_to_singles(runner, tmp_path):
    # AC-1: each entry of a batch equals the standalone single-id payload — the
    # batch amortizes start-up, it must not alter the per-card output.
    _family(tmp_path)
    single_2 = json.loads(
        runner.invoke(main, ["context", "HATS-2", *_args(tmp_path), "--json"]).output
    )
    single_3 = json.loads(
        runner.invoke(main, ["context", "HATS-3", *_args(tmp_path), "--json"]).output
    )
    batch = runner.invoke(main, ["context", "HATS-2", "HATS-3", *_args(tmp_path), "--json"])
    assert batch.exit_code == 0, batch.output
    payload = json.loads(batch.output)
    assert set(payload) == {"contexts"}
    assert list(payload["contexts"]) == ["HATS-2", "HATS-3"]  # input order preserved
    assert payload["contexts"]["HATS-2"] == single_2
    assert payload["contexts"]["HATS-3"] == single_3


def test_context_single_json_stays_unwrapped(runner, tmp_path):
    # AC-2: one id keeps the legacy unwrapped shape — no `contexts` envelope, so
    # existing single-id consumers are untouched.
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-2", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "contexts" not in payload
    assert payload["task"]["id"] == "HATS-2"


def test_context_batch_skip_and_continue_on_bad_id(runner, tmp_path):
    # A bad id yields a per-id error entry; the good cards still resolve, exit 0.
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-2", "HATS-404", "HATS-3", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    contexts = json.loads(result.output)["contexts"]
    assert contexts["HATS-2"]["task"]["id"] == "HATS-2"
    assert contexts["HATS-3"]["task"]["id"] == "HATS-3"
    assert contexts["HATS-404"]["error"]["code"] == "unknown_task"


def test_context_batch_dedups_preserving_order(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["context", "HATS-3", "HATS-2", "HATS-3", *_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert list(json.loads(result.output)["contexts"]) == ["HATS-3", "HATS-2"]


def test_context_no_ids_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_request"


def test_context_batch_human_separates_cards(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["context", "HATS-2", "HATS-3", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "id: HATS-2" in out and "id: HATS-3" in out
    assert out.count("tip:") == 1  # one shared tip footer, not one per card


# ----- ls: backlog scan (no id) ---------------------------------------------------


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


# ----- ls <ID>: neighbourhood graph walk (tree folded in here) --------------------


def test_ls_walk_depth_one_json(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["ls", "HATS-2", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["root"] == "HATS-2" and payload["depth"] == 1
    got = {(n["id"], n["kind"], n["direction"]) for n in payload["neighbors"]}
    assert got == {
        ("HATS-1", "parent_task", "out"),
        ("HATS-3", "depends_on", "out"),
        ("HATS-4", "related", "both"),
        ("HATS-5", "children", "in"),
    }


def test_ls_walk_deep_link_filter_is_the_tree(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main,
        [
            "ls",
            "HATS-1",
            "--deep",
            "2",
            "--link",
            "parent_task",
            "--link",
            "children",
            *_args(tmp_path),
            "--json",
        ],
    )
    payload = json.loads(result.output)
    assert [(n["id"], n["depth"]) for n in payload["neighbors"]] == [("HATS-2", 1), ("HATS-5", 2)]
    assert payload["neighbors"][-1]["path"] == ["HATS-1", "HATS-2", "HATS-5"]


def test_ls_walk_human_shows_direction_and_chain(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["ls", "HATS-1", "--deep", "2", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "neighbourhood (depth 2)" in result.output
    assert "← children" in result.output  # HATS-2 is a child of HATS-1
    assert "HATS-1 › HATS-2 › HATS-5" in result.output  # depth>1 prints the chain


def test_ls_walk_state_filter_prunes_output_not_traversal(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(
        main, ["ls", "HATS-1", "--deep", "2", "--state", "plan", *_args(tmp_path), "--json"]
    )
    ids = {n["id"] for n in json.loads(result.output)["neighbors"]}
    assert ids == {"HATS-2", "HATS-5"}  # T-5 reached THROUGH T-2 despite the filter


def test_ls_walk_unknown_root_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["ls", "HATS-404", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


def test_ls_deep_without_id_is_typed(runner, tmp_path):
    _family(tmp_path)
    result = runner.invoke(main, ["ls", "--deep", "2", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_request"


# ----- dead verbs: tree / audit / doc ls are unknown to the CLI (HATS-1029) -------


@pytest.mark.parametrize("argv", [["tree", "HATS-1"], ["audit", "HATS-1"], ["doc", "ls", "HATS-1"]])
def test_removed_verbs_are_unknown(runner, tmp_path, argv):
    _family(tmp_path)
    result = runner.invoke(main, [*argv, *_args(tmp_path)])
    assert result.exit_code != 0
    assert "No such command" in result.output


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


# ----- ls output cap (HATS-1047): cap 30 + --all, non-silent ----------------------


def _many(tmp_path, n):
    for i in range(n):
        make_card(tmp_path, f"HATS-{100 + i}", title=f"card {i}", state="brainstorm")


def _row_count(output):
    return sum(1 for ln in output.splitlines() if ln.strip().startswith("HATS-"))


def test_ls_scan_human_caps_at_30_with_footer(runner, tmp_path):
    _many(tmp_path, 42)
    result = runner.invoke(main, ["ls", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _row_count(result.output) == 30
    assert "showing 30 of 42" in result.output
    assert "--all" in result.output
    assert "42 task(s)" not in result.output  # uncapped footer suppressed when capped


def test_ls_scan_all_flag_bypasses_cap(runner, tmp_path):
    _many(tmp_path, 42)
    result = runner.invoke(main, ["ls", "--all", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _row_count(result.output) == 42
    assert "42 task(s)" in result.output
    assert "showing" not in result.output


def test_ls_scan_json_caps_with_total_and_capped(runner, tmp_path):
    _many(tmp_path, 42)
    payload = json.loads(runner.invoke(main, ["ls", *_args(tmp_path), "--json"]).output)
    assert len(payload["tasks"]) == 30
    assert payload["count"] == 30 and payload["total"] == 42 and payload["capped"] is True
    # --all -> full set, capped:false (map-not-filter: same shape, no truncation)
    full = json.loads(runner.invoke(main, ["ls", "--all", *_args(tmp_path), "--json"]).output)
    assert len(full["tasks"]) == 42 and full["count"] == 42
    assert full["total"] == 42 and full["capped"] is False


def test_ls_scan_json_small_is_not_capped(runner, tmp_path):
    _many(tmp_path, 3)
    payload = json.loads(runner.invoke(main, ["ls", *_args(tmp_path), "--json"]).output)
    assert payload["count"] == 3 and payload["total"] == 3 and payload["capped"] is False


def _wide_epic(tmp_path, n):
    make_card(tmp_path, "HATS-1", title="epic", state="execute")
    for i in range(n):
        make_card(tmp_path, f"HATS-{200 + i}", title=f"kid {i}", parent_task="HATS-1")


def test_ls_walk_human_caps_at_30(runner, tmp_path):
    _wide_epic(tmp_path, 40)
    result = runner.invoke(main, ["ls", "HATS-1", "--deep", "1", *_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.output.count("← children") == 30  # header line excluded
    assert "showing 30 of 40" in result.output
    assert "40 linked task(s)" not in result.output


def test_ls_walk_json_caps_and_all_bypasses(runner, tmp_path):
    _wide_epic(tmp_path, 40)
    capped = json.loads(
        runner.invoke(main, ["ls", "HATS-1", "--deep", "1", *_args(tmp_path), "--json"]).output
    )
    assert len(capped["neighbors"]) == 30
    assert capped["count"] == 30 and capped["total"] == 40 and capped["capped"] is True
    full = json.loads(
        runner.invoke(
            main, ["ls", "HATS-1", "--deep", "1", "--all", *_args(tmp_path), "--json"]
        ).output
    )
    assert len(full["neighbors"]) == 40 and full["capped"] is False


# ----- read path resolves the catalog's own backlog.yaml (HATS-1042) ----------

_CATALOG_BACKLOG = (
    "name: t\nprefix: HATS\n"
    "fsm:\n  initial: brainstorm\n"
    "  states: [{name: brainstorm}, {name: document}]\n"
    "  edges:\n    - {from: brainstorm, to: document}\n"
    "links:\n  kinds:\n"
    "    - {name: parent_task, arity: one, inverse: children}\n"
    "    - {name: children, derived: true, inverse: parent_task}\n"
    "    - {name: reviewed_with}\n"
)


def test_context_resolves_link_kinds_from_catalog_backlog_yaml(runner, tmp_path):
    # A catalog carrying its own backlog.yaml drives the read path's link kinds
    # (not the packaged default), matching the transition path (HATS-1042).
    catalog = tmp_path / "tasks"
    catalog.mkdir(parents=True)
    (catalog / "backlog.yaml").write_text(_CATALOG_BACKLOG)
    make_card(tmp_path, "HATS-1", links={"reviewed_with": ["HATS-2"]})
    make_card(tmp_path, "HATS-2", title="reviewer")
    result = runner.invoke(main, ["context", "HATS-1", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert "reviewed_with" in json.loads(result.output)["links"]


def test_context_packaged_default_omits_unconfigured_kind(runner, tmp_path):
    # Control: WITHOUT a catalog backlog.yaml, reviewed_with is not a configured
    # kind, so it never surfaces — the catalog file is what enables it above.
    make_card(tmp_path, "HATS-1", links={"reviewed_with": ["HATS-2"]})
    make_card(tmp_path, "HATS-2", title="reviewer")
    result = runner.invoke(main, ["context", "HATS-1", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert "reviewed_with" not in json.loads(result.output).get("links", {})


def test_ls_deep_resolves_link_kinds_from_catalog_backlog_yaml(runner, tmp_path):
    # The graph walk (ls --deep) reads the same catalog-resolved kinds.
    catalog = tmp_path / "tasks"
    catalog.mkdir(parents=True)
    (catalog / "backlog.yaml").write_text(_CATALOG_BACKLOG)
    make_card(tmp_path, "HATS-1", links={"reviewed_with": ["HATS-2"]})
    make_card(tmp_path, "HATS-2", title="reviewer")
    result = runner.invoke(main, ["ls", "HATS-1", "--deep", "1", *_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert "HATS-2" in {n["id"] for n in json.loads(result.output)["neighbors"]}


def test_context_read_fails_closed_on_project_root_links_yaml(runner):
    # R6 (ADR-0017 §1): the read path fails closed on a retired project-root
    # links.yaml too — isolate cwd so it resolves as the project root.
    with runner.isolated_filesystem() as cwd:
        cwd = Path(cwd)
        (cwd / "links.yaml").write_text("kinds:\n  - {name: x}\n")
        result = runner.invoke(
            main, ["context", "HATS-1", "--tasks-dir", str(cwd / "tasks"), "--json"]
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"]["code"] == "internal"
