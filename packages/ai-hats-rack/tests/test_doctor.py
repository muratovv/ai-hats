"""Backlog integrity report (HATS-1335): read-only checks over already-written
data. The strict-write guards (HATS-1327/1333) closed the SOURCE of broken
links; doctor inspects the STOCK — the 11 dangling parents + 1 dangling
depends_on of the motivating sweep were invisible to every existing tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.cardschema import build_card_schema
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.checks import CheckDeclaration
from ai_hats_rack.dispatch import AbortOperation
from ai_hats_rack.doctor import diagnose_bindings, diagnose_catalog
from ai_hats_rack.fsm import Topology
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


# ----- cycles ---------------------------------------------------------------


def test_transitive_cycle_reported_once_with_path(tasks_dir, tmp_path):
    # A->B->C->A on depends_on: exactly the shape HATS-1327's pair guard
    # deliberately does not catch.
    make_card(tasks_dir, "T-1", depends_on=["T-2"])
    make_card(tasks_dir, "T-2", depends_on=["T-3"])
    make_card(tasks_dir, "T-3", depends_on=["T-1"])
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    rows = by_check(findings, "link-cycle")
    assert len(rows) == 1
    assert rows[0].kind == "depends_on"
    assert rows[0].detail.count("T-1") == 2  # closed walk: T-1 -> T-2 -> T-3 -> T-1


def test_symmetric_kind_pair_is_not_a_cycle(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", related=["T-2"])
    make_card(tasks_dir, "T-2", related=["T-1"])
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    assert by_check(findings, "link-cycle") == []


# ----- duplicates -----------------------------------------------------------


def test_duplicate_ids_in_a_list_kind_are_reported(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2", depends_on=["T-1", "T-1"])
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    rows = by_check(findings, "duplicate-link")
    assert [(f.task_id, f.kind, f.target) for f in rows] == [("T-2", "depends_on", "T-1")]


# ----- required fields ------------------------------------------------------


def _gated_backlog(tmp_path):
    path = tmp_path / "backlog.yaml"
    path.write_text(
        "name: t\nprefix: T\n"
        + _MINIMAL_FSM
        + "links:\n  kinds:\n"
        + _KINDS
        + "fields:\n"
        + "  - {name: severity, type: str, required: true}\n"
        + "  - {name: resolution, type: str, default: '', required_on: [document]}\n"
    )
    return load_backlog(path)


def test_missing_required_and_state_gated_fields_reported(tasks_dir, tmp_path):
    defn = _gated_backlog(tmp_path)
    make_card(tasks_dir, "T-1")  # no severity at all
    make_card(tasks_dir, "T-2", state="document", severity="low")  # empty resolution
    make_card(tasks_dir, "T-3", state="brainstorm", severity="low")  # gate not entered
    findings = diagnose_catalog(tasks_dir, defn.links_registry, schema=build_card_schema(defn))
    rows = by_check(findings, "missing-field")
    assert {(f.task_id, f.kind) for f in rows} == {("T-1", "severity"), ("T-2", "resolution")}


# ----- mirror drift ---------------------------------------------------------

_MIRROR_KINDS = _KINDS + (
    "    - {name: supersedes, arity: one, inverse: superseded_by, handlers: [mirror-link]}\n"
    "    - {name: superseded_by, arity: one, inverse: supersedes, handlers: [mirror-link]}\n"
)


def test_stored_inverse_without_back_edge_is_mirror_drift(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", links={"supersedes": ["T-2"]})
    make_card(tasks_dir, "T-2")  # no superseded_by back-edge
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _MIRROR_KINDS))
    rows = by_check(findings, "mirror-drift")
    assert [(f.task_id, f.kind, f.target) for f in rows] == [("T-1", "supersedes", "T-2")]


def test_converged_mirror_pair_is_clean(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1", links={"supersedes": ["T-2"]})
    make_card(tasks_dir, "T-2", links={"superseded_by": ["T-1"]})
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _MIRROR_KINDS))
    assert by_check(findings, "mirror-drift") == []


def test_derived_inverse_is_not_mirror_checked(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2", parent_task="T-1")  # children is derived, no drift
    findings = diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS))
    assert by_check(findings, "mirror-drift") == []


# ----- clean / workspace ----------------------------------------------------


def test_healthy_catalog_yields_no_findings(tasks_dir, tmp_path):
    make_card(tasks_dir, "T-1")
    make_card(tasks_dir, "T-2", parent_task="T-1", depends_on=["T-1"], related=["T-1"])
    assert diagnose_catalog(tasks_dir, _registry(tmp_path, _KINDS)) == []
    assert diagnose_catalog(tmp_path / "empty", _registry(tmp_path, _KINDS)) == []


_HYP_DEF = (
    "name: hypotheses\nprefix: HYP\ncli_alias: hyp\n"
    + _MINIMAL_FSM
    + "links:\n  kinds:\n"
    + "    - {name: source_task, arity: one, targets: tasks}\n"
)


def _two_backlog_workspace(tmp_path):
    from ai_hats_rack.resolver import RackRoot
    from ai_hats_rack.workspace import Workspace

    project = tmp_path / "proj"
    tasks = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    hyp = project / ".agent" / "ai-hats" / "tracker" / "hypotheses"
    hyp.mkdir(parents=True)
    (hyp / "backlog.yaml").write_text(_HYP_DEF, encoding="utf-8")
    return Workspace.discover(
        [RackRoot(project_dir=project, tasks_dir=tasks, backlog_owner=project, prefix="HATS")]
    ), (
        tasks,
        hyp,
    )


def test_workspace_scan_labels_backlogs_and_routes_cross_refs(tmp_path):
    from ai_hats_rack.doctor import diagnose_workspace

    workspace, (tasks, hyp) = _two_backlog_workspace(tmp_path)
    make_card(tasks, "HATS-1", parent_task="HATS-404")
    make_card(hyp, "HYP-1", links={"source_task": ["HATS-1"]})  # resolves cross-backlog
    make_card(hyp, "HYP-2", links={"source_task": ["HATS-404"]})  # dangling cross-backlog
    findings = diagnose_workspace(workspace)
    assert {(f.backlog, f.task_id, f.check, f.target) for f in findings} == {
        ("tasks", "HATS-1", "dangling-link", "HATS-404"),
        ("hyp", "HYP-2", "dangling-link", "HATS-404"),
    }


# ----- the check channel (HATS-1584) -----------------------------------------


class _Defn:
    def __init__(self, topology, cli_alias: str = "") -> None:
        self.topology = topology
        self.cli_alias = cli_alias


class _Instance:
    def __init__(self, name, definition, catalog, is_tasks=False) -> None:
        self.name = name
        self.definition = definition
        self.catalog = catalog
        self.is_tasks = is_tasks


class _Workspace:
    """Only what :func:`diagnose_bindings` reads: the mounted instances and the
    integrator's per-catalog port factory."""

    def __init__(self, *instances, check_port=None) -> None:
        self.instances = instances
        self.check_port = check_port


class _Port:
    def __init__(self, *rows, error: Exception | None = None) -> None:
        self._rows = rows
        self._error = error
        self.asked = 0

    def check_declarations(self):
        self.asked += 1
        if self._error is not None:
            raise self._error
        return self._rows


def _tasks_topology() -> Topology:
    return Topology(
        initial="open",
        states=("open", "review", "done"),
        edges={"open": ("review",), "review": ("done",), "done": ()},
    )


def _hyp_topology() -> Topology:
    return Topology(
        initial="active",
        states=("active", "confirmed"),
        edges={"active": ("confirmed",), "confirmed": ()},
    )


def _workspace(port=None, tmp_path: Path = Path("/nowhere")) -> _Workspace:
    return _Workspace(
        _Instance("tasks", _Defn(_tasks_topology()), tmp_path / "tasks", is_tasks=True),
        _Instance("hypotheses", _Defn(_hyp_topology(), "hyp"), tmp_path / "hyp"),
        check_port=(lambda _catalog: port) if port is not None else None,
    )


def _row(point: str, *, backlog: str = "tasks") -> CheckDeclaration:
    return CheckDeclaration(
        path=(backlog,),
        at=(point,),
        cargo={},
        on_error="refuse",
        label="'role' binds skill/gate.sh under apps.rack",
        handle=None,
    )


def _report(*rows, owner: Path | None = Path("/proj"), error: Exception | None = None, port=...):
    used = _Port(*rows, error=error) if port is ... else port
    return diagnose_bindings(_workspace(used), owner=owner)


def test_a_dead_point_is_a_finding_the_report_carries():
    """The whole reason this section exists: a point no mounted topology has is
    a gate that never fires, and until now nothing said so anywhere."""
    report = _report(_row("reviw->done"))

    assert [(r.status, r.selector) for r in report.rows] == [("dead", "reviw->done")]
    assert [f.check for f in report.findings] == ["dead-check-point"]
    assert "reviw->done" in report.findings[0].detail


def test_a_sibling_backlogs_arrow_under_this_backlog_is_a_finding():
    """HATS-1774 reverses what HATS-1584 decided here. The skip R10 made legal is
    a row that arms under its OWN backlog; this one is addressed to ``tasks`` and
    written in the hypotheses grammar, so it fires nowhere — and calling that a
    legal skip is what let a declared gate be a silent no-op with a clean report.
    """
    report = _report(_row("active->confirmed"))

    assert [r.status for r in report.rows] == ["dead"]
    assert [f.check for f in report.findings] == ["dead-check-point"]
    assert "apps.rack.hyp" in report.findings[0].detail


def test_a_row_no_mounted_backlog_answers_to_is_a_finding():
    report = _report(_row("review->done", backlog="cards"))

    assert [r.status for r in report.rows] == ["unaddressed"]
    assert [f.check for f in report.findings] == ["unaddressable-check-row"]


def test_rows_that_cannot_be_read_become_a_finding_not_an_exception():
    """A role whose row is broken is exactly what this section reports, so the
    failure to resolve one must not take the report down with it."""
    report = _report(error=AbortOperation("checks: script not found at /nope/gate.sh"))

    assert [f.check for f in report.findings] == ["unreadable-check-rows"]
    assert "/nope/gate.sh" in report.findings[0].detail
    assert report.rows == ()


def test_the_port_is_asked_once_for_the_whole_project():
    """Every mounted catalog answers with the same rows — they come from the
    project that owns the backlog — so asking per catalog would re-compose the
    role once per backlog for one identical answer."""
    port = _Port(_row("review->done"))

    diagnose_bindings(_workspace(port), owner=Path("/proj"))

    assert port.asked == 1


def test_an_integrator_with_no_check_port_is_said_out_loud():
    """A bare rack has nothing to declare a gate with (ADR-0019 D11 clause 5).
    Not a defect — but not an armed gate either, so the roster says why it is
    empty instead of reading as "nothing declared"."""
    report = _report(port=None)

    assert report.rows == () and report.findings == ()
    assert "no integrator supplies a check executor" in report.note


def test_an_ownerless_backlog_says_that_rather_than_no_rows():
    """The port answers "no rows" for a backlog nobody owns, which is a
    different fact from a role that declares none — and only one of the two is
    about the role."""
    report = _report(_row("review->done"), owner=None)

    assert report.rows == ()
    assert "no project owns this backlog" in report.note


def test_a_role_that_declares_nothing_says_so_too():
    report = _report()

    assert report.rows == ()
    assert "no row is declared under apps.rack" in report.note
