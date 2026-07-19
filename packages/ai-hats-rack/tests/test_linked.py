"""linked.py: link/unlink transactions, neighbourhood walk, ls scan, context
package (HATS-1024; read surface v2 HATS-1029)."""

from __future__ import annotations

import pytest

from ai_hats_rack.kernel import UnknownTaskError
from ai_hats_rack.linked import (
    SelfLinkError,
    build_context,
    card_filter,
    link,
    scan_cards,
    unlink,
    walk_neighborhood,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.models import TaskCard
from ai_hats_rack.registry import DerivedLinkKindError, UnknownLinkKindError

# A trivially valid fsm block so the custom kinds are the only variable.
_MINIMAL_FSM = (
    "fsm:\n"
    "  initial: brainstorm\n"
    "  states: [{name: brainstorm}, {name: document}]\n"
    "  edges:\n    - {from: brainstorm, to: document}\n"
)

_REVIEWED_WITH_KINDS = (
    "    - {name: parent_task, arity: one, inverse: children}\n"
    "    - {name: depends_on}\n"
    "    - {name: related, inverse: related}\n"
    "    - {name: children, derived: true, inverse: parent_task}\n"
    "    - {name: reviewed_with}\n"
)


def _backlog_registry(tmp_path, kinds_block):
    """The links registry from a full backlog.yaml wrapping ``kinds_block``
    (links.yaml override retired, HATS-1042)."""
    path = tmp_path / "backlog.yaml"
    path.write_text("name: t\nprefix: T\n" + _MINIMAL_FSM + "links:\n  kinds:\n" + kinds_block)
    return load_backlog(path).links_registry


def make_card(tasks_dir, task_id, **fields):
    work = fields.pop("work", ())
    card = TaskCard(id=task_id, **fields)
    for msg in work:
        card.log_work(msg)
    path = tasks_dir / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)
    return card


def load(tasks_dir, task_id) -> TaskCard:
    return TaskCard.from_yaml(tasks_dir / task_id / "task.yaml")


# ----- link -------------------------------------------------------------------


def test_link_depends_persists_and_logs(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    result = link(tasks_dir, "T-1", "T-2", "depends", actor="tester")
    assert result.changed and result.kinds == ("depends_on",)
    card = load(tasks_dir, "T-1")
    assert card.depends_on == ["T-2"]
    assert card.updated  # bumped by the persist
    assert "[tester] Linked T-2 (depends_on)" in card.work_log[-1].message or (
        "Linked T-2 (depends_on)" in card.work_log[-1].message
    )


def test_link_default_kind_is_related(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    result = link(tasks_dir, "T-1", "T-2")
    assert result.kinds == ("related",)
    assert load(tasks_dir, "T-1").related == ["T-2"]


def test_link_accepts_depends_on_alias(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    result = link(tasks_dir, "T-1", "T-2", "depends_on")
    assert result.kinds == ("depends_on",)


def test_link_is_idempotent(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    link(tasks_dir, "T-1", "T-2", "depends")
    result = link(tasks_dir, "T-1", "T-2", "depends")
    assert not result.changed and result.kinds == ()
    card = load(tasks_dir, "T-1")
    assert card.depends_on == ["T-2"]
    assert len(card.work_log) == 1  # the no-op wrote nothing


def test_link_self_is_refused(tasks_dir):
    make_card(tasks_dir, "T-1")
    with pytest.raises(SelfLinkError):
        link(tasks_dir, "T-1", "T-1")


def test_link_unknown_target_is_refused(tasks_dir):
    make_card(tasks_dir, "T-1")
    with pytest.raises(UnknownTaskError) as err:
        link(tasks_dir, "T-1", "T-404")
    assert err.value.task_id == "T-404"


def test_link_unknown_source_is_refused(tasks_dir):
    make_card(tasks_dir, "T-2")
    with pytest.raises(UnknownTaskError):
        link(tasks_dir, "T-404", "T-2")


def test_link_unknown_kind_is_typed(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    with pytest.raises(UnknownLinkKindError) as err:
        link(tasks_dir, "T-1", "T-2", "blocks")
    # the refusal names the configured set so the caller can self-correct
    assert set(err.value.configured) == {"parent_task", "children", "depends_on", "related"}


def test_link_arbitrary_new_kind_lands_in_links_dict(tasks_dir, tmp_path):
    # A configured kind whose name is not a dedicated field is stored under the
    # generic `links:` key — the extras-compatible channel new kinds ride (HATS-1028).
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    reg = _backlog_registry(
        tmp_path,
        "    - {name: parent_task, arity: one, inverse: children}\n"
        "    - {name: children, derived: true, inverse: parent_task}\n"
        "    - {name: depends_on}\n"
        "    - {name: related, inverse: related}\n"
        "    - {name: reviewed_with}\n",
    )
    result = link(tasks_dir, "T-1", "T-2", "reviewed_with", registry=reg)
    assert result.changed and result.kinds == ("reviewed_with",)
    card = load(tasks_dir, "T-1")
    assert card.links == {"reviewed_with": ["T-2"]}
    assert card.depends_on == [] and card.related == []  # dedicated fields untouched


def test_link_parent_kind_sets_scalar(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    result = link(tasks_dir, "T-1", "T-2", "parent_task")
    assert result.changed and result.kinds == ("parent_task",)
    assert load(tasks_dir, "T-1").parent_task == "T-2"


def test_link_derived_kind_is_refused(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2")
    with pytest.raises(DerivedLinkKindError) as err:
        link(tasks_dir, "T-1", "T-2", "children")
    assert err.value.inverse == "parent_task"


# ----- unlink -----------------------------------------------------------------


def test_unlink_removes_and_logs(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-2"])
    make_card(tasks_dir, "T-2")
    result = unlink(tasks_dir, "T-1", "T-2", "depends")
    assert result.changed and result.kinds == ("depends_on",)
    card = load(tasks_dir, "T-1")
    assert card.depends_on == []
    assert "Unlinked T-2 (depends_on)" in card.work_log[-1].message


def test_unlink_absent_is_noop(tasks_dir):
    make_card(tasks_dir, "T-1")
    result = unlink(tasks_dir, "T-1", "T-2")
    assert not result.changed and result.kinds == ()
    assert load(tasks_dir, "T-1").work_log == []


def test_unlink_defaults_to_both_kinds(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-2"], related=["T-2"])
    result = unlink(tasks_dir, "T-1", "T-2")
    assert set(result.kinds) == {"depends_on", "related"}
    card = load(tasks_dir, "T-1")
    assert card.depends_on == [] and card.related == []


def test_unlink_kind_scoped_leaves_the_other(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-2"], related=["T-2"])
    unlink(tasks_dir, "T-1", "T-2", "depends")
    card = load(tasks_dir, "T-1")
    assert card.depends_on == [] and card.related == ["T-2"]


def test_unlink_dangling_target_is_removable(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-GONE"])
    result = unlink(tasks_dir, "T-1", "T-GONE")
    assert result.changed
    assert load(tasks_dir, "T-1").depends_on == []


# ----- neighbourhood walk -----------------------------------------------------


def _graph(tasks_dir):
    """Epic T-1 ⇄ task T-2 (parent) ; T-2 depends_on T-3, related T-4 ; kid T-5."""
    make_card(tasks_dir, "T-1", title="epic", state="execute", priority="high")
    make_card(tasks_dir, "T-2", title="task", state="plan", parent_task="T-1",
              depends_on=["T-3"], related=["T-4"])
    make_card(tasks_dir, "T-3", title="dep", state="done")
    make_card(tasks_dir, "T-4", title="rel", state="execute")
    make_card(tasks_dir, "T-5", title="kid", state="plan", parent_task="T-2")


def test_walk_depth_one_lists_edges_with_direction(tasks_dir):
    _graph(tasks_dir)
    rows = walk_neighborhood(tasks_dir, "T-2", depth=1)
    seen = {(n.id, n.kind, n.direction, n.depth) for n in rows}
    assert seen == {
        ("T-1", "parent_task", "out", 1),
        ("T-3", "depends_on", "out", 1),
        ("T-4", "related", "both", 1),
        ("T-5", "children", "in", 1),
    }
    assert all(n.path == ("T-2", n.id) for n in rows)


def test_walk_depth_two_reaches_grandchildren(tasks_dir):
    _graph(tasks_dir)
    rows = walk_neighborhood(tasks_dir, "T-1", depth=2, link_patterns=("parent_task", "children"))
    # T-1 → T-2 (child, d1) → T-5 (grandchild, d2); the back-edge to T-1 is NOT re-crossed
    assert [(n.id, n.depth) for n in rows] == [("T-2", 1), ("T-5", 2)]
    t5 = rows[-1]
    assert t5.path == ("T-1", "T-2", "T-5") and t5.kind == "children"


def test_walk_never_crosses_one_edge_twice(tasks_dir):
    # parent↔children is ONE undirected edge: from the epic we reach the child,
    # and the child's parent edge back to the epic is deduped (never re-listed).
    _graph(tasks_dir)
    rows = walk_neighborhood(tasks_dir, "T-1", depth=3, link_patterns=("parent_task", "children"))
    assert "T-1" not in {n.id for n in rows}  # the root is never re-emitted


def test_walk_related_cycle_terminates(tasks_dir):
    make_card(tasks_dir, "T-1", title="a", related=["T-2"])
    make_card(tasks_dir, "T-2", title="b", related=["T-1"])
    rows = walk_neighborhood(tasks_dir, "T-1", depth=5)
    # symmetric related A↔B is one edge — T-2 shows once, and it does not loop back
    assert [n.id for n in rows] == ["T-2"]


def test_walk_link_pattern_filters_traversed_kinds(tasks_dir):
    _graph(tasks_dir)
    rows = walk_neighborhood(tasks_dir, "T-2", depth=1, link_patterns=("depends_on", "related"))
    assert {n.id for n in rows} == {"T-3", "T-4"}


def test_walk_row_filter_prunes_output_but_not_traversal(tasks_dir):
    # T-5 (plan) is reachable only THROUGH T-2 (plan). Filtering to state=plan
    # still traverses T-2 to surface T-5; T-1/T-3/T-4 (other states) drop out.
    _graph(tasks_dir)
    rows = walk_neighborhood(
        tasks_dir, "T-1", depth=2, row_filter=card_filter(state="plan")
    )
    assert {n.id for n in rows} == {"T-2", "T-5"}


def test_walk_dangling_edge_is_skipped(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-GONE"])
    assert walk_neighborhood(tasks_dir, "T-1", depth=1) == []


def test_walk_unknown_root_is_typed(tasks_dir):
    with pytest.raises(UnknownTaskError):
        walk_neighborhood(tasks_dir, "T-404")


# ----- ls scan ------------------------------------------------------------------


def test_scan_filters_and_combine(tasks_dir):
    make_card(
        tasks_dir,
        "T-1",
        title="Fix retry loop",
        description="watcher",
        state="plan",
        tags=["infra"],
    )
    make_card(
        tasks_dir,
        "T-2",
        title="Docs",
        description="retry mention",
        state="done",
        tags=["docs"],
        parent_task="T-1",
    )
    make_card(tasks_dir, "T-3", title="Other", state="plan")
    assert [r.id for r in scan_cards(tasks_dir, grep="RETRY")] == ["T-1", "T-2"]
    assert [r.id for r in scan_cards(tasks_dir, grep="retry", state="plan")] == ["T-1"]
    assert [r.id for r in scan_cards(tasks_dir, tag="docs")] == ["T-2"]
    assert [r.id for r in scan_cards(tasks_dir, parent="T-1")] == ["T-2"]
    assert [r.id for r in scan_cards(tasks_dir)] == ["T-1", "T-2", "T-3"]


def test_scan_skips_corrupt_card(tasks_dir):
    make_card(tasks_dir, "T-1")
    bad = tasks_dir / "T-2" / "task.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text("{ not yaml: [")
    assert [r.id for r in scan_cards(tasks_dir)] == ["T-1"]


# ----- context package -----------------------------------------------------------


def _family(tasks_dir):
    """Epic T-1 (plan.md) ← task T-2 (docs, links, child T-5)."""
    make_card(tasks_dir, "T-1", title="epic", state="execute")
    (tasks_dir / "T-1" / "plan.md").write_text("# epic plan")
    make_card(
        tasks_dir,
        "T-2",
        title="the task",
        state="plan",
        description="do the thing",
        parent_task="T-1",
        depends_on=["T-3"],
        related=["T-4"],
        work=["started"],
    )
    (tasks_dir / "T-2" / "notes.md").write_text("notes")
    make_card(tasks_dir, "T-3", title="dep", state="done", resolution="merged")
    (tasks_dir / "T-3" / "summary.md").write_text("dep summary body")
    (tasks_dir / "T-3" / "retro.md").write_text("dep retro body")
    make_card(tasks_dir, "T-4", title="rel", state="execute")
    make_card(tasks_dir, "T-5", title="kid", state="plan", parent_task="T-2")


def test_context_full_package(tasks_dir):
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2")
    assert pkg.task.id == "T-2"
    assert [d.name for d in pkg.documents] == ["notes.md"]
    # one top-level links map, registry order: parent_task, depends_on, related, children
    assert list(pkg.links) == ["parent_task", "depends_on", "related", "children"]
    (parent,) = pkg.links["parent_task"]
    assert parent.id == "T-1"
    assert [d.name for d in parent.docs] == ["plan.md"]
    assert parent.docs[0].path.is_absolute()
    assert parent.docs[0].mtime.endswith("Z")
    (dep,) = pkg.links["depends_on"]
    assert (dep.id, dep.state, dep.resolution) == ("T-3", "done", "merged")
    assert [d.name for d in dep.docs] == ["summary.md", "retro.md"]
    (rel,) = pkg.links["related"]
    assert rel.id == "T-4" and rel.docs == ()
    assert [c.id for c in pkg.links["children"]] == ["T-5"]
    assert pkg.included == ()


def test_context_root_card_rides_in_full(tasks_dir):
    # HATS-1031 Р11: the ROOT card is the whole card (`show` parity); trimming
    # stays a linked-card (LinkView) discipline only.
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2")
    head = pkg.to_dict()["task"]
    assert head == pkg.task.to_dict()
    assert [e["message"] for e in head["work_log"]] == ["started"]
    assert {"reviewer", "assignee", "role", "created", "updated"} <= set(head)


def test_context_dangling_link_is_skipped(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-404"])
    pkg = build_context(tasks_dir, "T-1")
    assert "depends_on" not in pkg.links


def test_context_dedups_ids_across_kinds(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-2"], related=["T-2"])
    make_card(tasks_dir, "T-2")
    pkg = build_context(tasks_dir, "T-1")
    assert len(pkg.links["depends_on"]) == 1 and "related" not in pkg.links


def test_context_new_kind_surfaces_in_links(tasks_dir, tmp_path):
    # A configured kind not named after a dedicated field rides the generic
    # `links:` dict and still appears (after the dedicated-field kinds) in context.
    make_card(tasks_dir, "T-1", links={"reviewed_with": ["T-2"]})
    make_card(tasks_dir, "T-2", title="reviewer note")
    pkg = build_context(
        tasks_dir, "T-1", registry=_backlog_registry(tmp_path, _REVIEWED_WITH_KINDS)
    )
    (view,) = pkg.links["reviewed_with"]
    assert view.id == "T-2" and view.title == "reviewer note"


def test_context_unknown_task_is_typed(tasks_dir):
    with pytest.raises(UnknownTaskError):
        build_context(tasks_dir, "T-404")


# ----- --with inclusions ----------------------------------------------------------


def test_with_pattern_embeds_own_doc(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("my plan body")
    pkg = build_context(tasks_dir, "T-2", with_patterns=("plan*",))
    own = next(i for i in pkg.included if i.task_id == "T-2")
    assert own.name == "plan.md" and own.content == "my plan body" and not own.truncated


def test_with_no_pattern_embeds_nothing(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("body")
    assert build_context(tasks_dir, "T-2").included == ()


def test_with_pattern_matches_own_and_linked_docs(tasks_dir):
    # repeatable `--with plan* --with summary*` spans the task's own docs AND the
    # linked-task docs context lists: own plan.md + parent T-1 plan.md + dep T-3 summary.md.
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("own plan")
    pkg = build_context(tasks_dir, "T-2", with_patterns=("plan*", "summary*"))
    got = {(i.task_id, i.name) for i in pkg.included}
    assert got == {("T-2", "plan.md"), ("T-1", "plan.md"), ("T-3", "summary.md")}


def test_with_pattern_no_match_is_graceful(tasks_dir):
    _family(tasks_dir)
    assert build_context(tasks_dir, "T-2", with_patterns=("ghost*",)).included == ()


def test_with_pattern_matches_arbitrary_named_doc(tasks_dir):
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2", with_patterns=("notes.md",))
    (inc,) = pkg.included
    assert inc.name == "notes.md" and inc.content == "notes"


def test_with_truncates_at_max_bytes_and_flags(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("x" * 500)
    pkg = build_context(tasks_dir, "T-2", with_patterns=("plan*",), max_bytes=100)
    own = next(i for i in pkg.included if i.task_id == "T-2")
    assert own.truncated and own.size == 500 and len(own.content) == 100


def test_with_star_embeds_each_doc_once(tasks_dir):
    # `*` spans own (notes.md, plan.md) + parent (T-1 plan.md) + dep (T-3
    # summary.md, retro.md); every (owner, name) is embedded exactly once.
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("p")
    pkg = build_context(tasks_dir, "T-2", with_patterns=("*",))
    keys = [(i.task_id, i.name) for i in pkg.included]
    assert len(keys) == len(set(keys))
    assert set(keys) == {
        ("T-2", "notes.md"),
        ("T-2", "plan.md"),
        ("T-1", "plan.md"),
        ("T-3", "summary.md"),
        ("T-3", "retro.md"),
    }
