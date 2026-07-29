"""Backlog integrity report (HATS-1335): read-only checks over already-written
data. The strict-write guards (HATS-1327/1333) closed the SOURCE of broken
links; doctor inspects the STOCK — the 11 dangling parents + 1 dangling
depends_on of the motivating sweep were invisible to every existing tool."""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import load_backlog
from ai_hats_rack.doctor import diagnose_catalog
from ai_hats_rack.models import TaskCard

# A trivially valid fsm block so the custom kinds are the only variable
# (test_linked.py precedent).
_MINIMAL_FSM = (
    "fsm:\n"
    "  initial: brainstorm\n"
    "  states: [{name: brainstorm}, {name: document}]\n"
    "  edges:\n    - {from: brainstorm, to: document}\n"
)


def _registry(tmp_path, kinds_block):
    path = tmp_path / "backlog.yaml"
    path.write_text("name: t\nprefix: T\n" + _MINIMAL_FSM + "links:\n  kinds:\n" + kinds_block)
    return load_backlog(path).links_registry


_KINDS = (
    "    - {name: parent_task, arity: one, inverse: children}\n"
    "    - {name: depends_on}\n"
    "    - {name: related, inverse: related}\n"
    "    - {name: children, derived: true, inverse: parent_task}\n"
)


@pytest.fixture
def tasks_dir(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    return d


def make_card(tasks_dir, task_id, **fields):
    card = TaskCard(id=task_id, **fields)
    path = tasks_dir / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)
    return card


def by_check(findings, check):
    return [f for f in findings if f.check == check]


# ----- unreadable cards ---------------------------------------------------


def test_corrupt_task_yaml_is_a_finding_not_a_silent_skip(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1")
    broken = tasks_dir / "T-2"
    broken.mkdir()
    (broken / "task.yaml").write_text("{not: [valid")
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    rows = by_check(findings, "unreadable-card")
    assert [f.task_id for f in rows] == ["T-2"]
    assert "load" in rows[0].detail


def test_card_directory_without_task_yaml_is_a_finding(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1")
    (tasks_dir / "T-9").mkdir()
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    rows = by_check(findings, "unreadable-card")
    assert [f.task_id for f in rows] == ["T-9"]
    assert "no task.yaml" in rows[0].detail


# ----- dangling links -----------------------------------------------------


def test_dangling_refs_reported_per_kind_with_target(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", parent_task="1")  # the HATS-1096..1104 shape
    make_card(tasks_dir, "T-2", depends_on=["T-1", "t-404"])
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    rows = by_check(findings, "dangling-link")
    assert {(f.task_id, f.kind, f.target) for f in rows} == {
        ("T-1", "parent_task", "1"),
        ("T-2", "depends_on", "t-404"),
    }


def test_empty_parent_means_no_parent_never_dangling(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", parent_task="")
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    assert by_check(findings, "dangling-link") == []


def test_unreadable_target_exists_on_disk_so_not_dangling(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", depends_on=["T-2"])
    broken = tasks_dir / "T-2"
    broken.mkdir()
    (broken / "task.yaml").write_text("{not: [valid")
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    assert by_check(findings, "dangling-link") == []
    assert [f.task_id for f in by_check(findings, "unreadable-card")] == ["T-2"]


def test_cross_backlog_kind_routes_through_injected_checker(tasks_dir, tmp_path):
    kinds = _KINDS + "    - {name: source_task, arity: one, targets: tasks}\n"
    make_card(tasks_dir, "T-1", links={"source_task": ["HATS-1"]})
    make_card(tasks_dir, "T-2", links={"source_task": ["HATS-404"]})
    seen = []

    def checker(target, targets):
        seen.append((target, targets))
        return target == "HATS-1"

    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, kinds), exists=checker)
    rows = by_check(findings, "dangling-link")
    assert [(f.task_id, f.target) for f in rows] == [("T-2", "HATS-404")]
    assert set(seen) == {("HATS-1", "tasks"), ("HATS-404", "tasks")}
