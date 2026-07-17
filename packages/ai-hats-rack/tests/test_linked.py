"""linked.py: link/unlink transactions, tree, ls scan, context package (HATS-1024)."""

from __future__ import annotations

import pytest

from ai_hats_rack.docstore import UnknownDocumentError
from ai_hats_rack.kernel import UnknownTaskError
from ai_hats_rack.linked import (
    SelfLinkError,
    UnknownLinkKindError,
    UnknownSelectorError,
    build_context,
    build_tree,
    link,
    scan_cards,
    unlink,
)
from ai_hats_rack.models import TaskCard


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
    with pytest.raises(UnknownLinkKindError):
        link(tasks_dir, "T-1", "T-2", "blocks")


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


# ----- tree -------------------------------------------------------------------


def test_tree_two_levels(tasks_dir):
    make_card(tasks_dir, "T-1", title="epic", state="execute", priority="high")
    make_card(tasks_dir, "T-2", title="child a", parent_task="T-1", state="done")
    make_card(tasks_dir, "T-3", title="child b", parent_task="T-1")
    make_card(tasks_dir, "T-4", title="grandchild", parent_task="T-2", state="plan")
    tree = build_tree(tasks_dir, "T-1")
    assert (tree.id, tree.state, tree.priority, tree.title) == ("T-1", "execute", "high", "epic")
    assert [c.id for c in tree.children] == ["T-2", "T-3"]
    assert [g.id for g in tree.children[0].children] == ["T-4"]
    assert tree.children[0].children[0].state == "plan"


def test_tree_orders_children_numerically(tasks_dir):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-999", parent_task="T-1")
    make_card(tasks_dir, "T-1020", parent_task="T-1")
    tree = build_tree(tasks_dir, "T-1")
    assert [c.id for c in tree.children] == ["T-999", "T-1020"]


def test_tree_cycle_is_shown_once(tasks_dir):
    make_card(tasks_dir, "T-1", parent_task="T-2")
    make_card(tasks_dir, "T-2", parent_task="T-1")
    tree = build_tree(tasks_dir, "T-1")
    assert [c.id for c in tree.children] == ["T-2"]
    assert tree.children[0].children == ()


def test_tree_unknown_root_is_typed(tasks_dir):
    with pytest.raises(UnknownTaskError):
        build_tree(tasks_dir, "T-404")


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
    assert pkg.parent is not None and pkg.parent.id == "T-1"
    assert [d.name for d in pkg.parent.docs] == ["plan.md"]
    assert pkg.parent.docs[0].path.is_absolute()
    assert pkg.parent.docs[0].mtime.endswith("Z")
    (dep,) = pkg.depends_on
    assert (dep.id, dep.state, dep.resolution) == ("T-3", "done", "merged")
    assert [d.name for d in dep.docs] == ["summary.md", "retro.md"]
    (rel,) = pkg.related
    assert rel.id == "T-4" and rel.docs == ()
    assert [c.id for c in pkg.children] == ["T-5"]
    assert pkg.included == ()


def test_context_trimmed_card_carries_latest_work_log_only(tasks_dir):
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2")
    head = pkg.to_dict()["task"]
    assert head["latest_work_log"]["message"] == "started"
    assert "work_log" not in head


def test_context_dangling_link_is_skipped(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-404"])
    pkg = build_context(tasks_dir, "T-1")
    assert pkg.depends_on == ()


def test_context_dedups_ids_across_kinds(tasks_dir):
    make_card(tasks_dir, "T-1", depends_on=["T-2"], related=["T-2"])
    make_card(tasks_dir, "T-2")
    pkg = build_context(tasks_dir, "T-1")
    assert len(pkg.depends_on) == 1 and pkg.related == ()


def test_context_unknown_task_is_typed(tasks_dir):
    with pytest.raises(UnknownTaskError):
        build_context(tasks_dir, "T-404")


# ----- --with inclusions ----------------------------------------------------------


def test_with_plan_embeds_own_plan(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("my plan body")
    pkg = build_context(tasks_dir, "T-2", selectors=["plan"])
    (inc,) = pkg.included
    assert (inc.task_id, inc.name) == ("T-2", "plan.md")
    assert inc.content == "my plan body" and not inc.truncated


def test_with_plan_missing_is_graceful(tasks_dir):
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2", selectors=["plan"])
    assert pkg.included == ()


def test_with_summary_embeds_own_and_linked(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "summary.md").write_text("own summary")
    pkg = build_context(tasks_dir, "T-2", selectors=["summary"])
    assert [(i.task_id, i.name) for i in pkg.included] == [
        ("T-2", "summary.md"),
        ("T-3", "summary.md"),
    ]
    assert pkg.included[1].content == "dep summary body"


def test_with_doc_selector_embeds_named_doc(tasks_dir):
    _family(tasks_dir)
    pkg = build_context(tasks_dir, "T-2", selectors=["doc:notes.md"])
    (inc,) = pkg.included
    assert inc.name == "notes.md" and inc.content == "notes"


def test_with_doc_unknown_name_is_typed(tasks_dir):
    _family(tasks_dir)
    with pytest.raises(UnknownDocumentError):
        build_context(tasks_dir, "T-2", selectors=["doc:ghost.md"])


def test_with_unknown_selector_is_typed(tasks_dir):
    _family(tasks_dir)
    with pytest.raises(UnknownSelectorError):
        build_context(tasks_dir, "T-2", selectors=["everything"])


def test_with_truncates_at_max_bytes_and_flags(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("x" * 500)
    pkg = build_context(tasks_dir, "T-2", selectors=["plan"], max_bytes=100)
    (inc,) = pkg.included
    assert inc.truncated and inc.size == 500 and len(inc.content) == 100


def test_with_duplicate_selectors_embed_once(tasks_dir):
    _family(tasks_dir)
    (tasks_dir / "T-2" / "plan.md").write_text("p")
    pkg = build_context(tasks_dir, "T-2", selectors=["plan", "plan", "doc:plan.md"])
    assert len(pkg.included) == 1
