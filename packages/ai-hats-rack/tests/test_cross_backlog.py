"""HATS-1044 step 2: cross-backlog link-target existence (ADR-0017 §2/R4).

A kind with ``targets: <backlog>`` checks that catalog through the workspace
existence seam; a kind WITHOUT ``targets`` checks its own catalog (today's
behavior, zero change). All routing rides the one injected checker — in-lock
handlers never hold a workspace handle (one-lock rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.kernel import UnknownTaskError
from ai_hats_rack.ops import parse_ops
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import Workspace

_HYP = """\
name: hypotheses
prefix: HYP
fsm:
  initial: active
  states: [{name: active}, {name: confirmed}]
  edges: [{from: active, to: confirmed}]
links:
  kinds:
    - {name: source_task, arity: one, targets: tasks}
    - {name: related, arity: many}
"""

_TAIL = Path("tracker") / "backlog" / "tasks"


def _seed(catalog: Path, task_id: str, *, state: str = "active") -> None:
    d = catalog / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.yaml").write_text(f"id: {task_id}\ntitle: seed\nstate: {state}\n", encoding="utf-8")


@pytest.fixture
def workspace(tmp_path) -> tuple[Workspace, RackRoot]:
    project = tmp_path / "proj"
    tasks = project / ".agent" / "ai-hats" / _TAIL
    tasks.mkdir(parents=True)
    hyp = project / ".agent" / "ai-hats" / "tracker" / "hypotheses"
    hyp.mkdir(parents=True)
    (hyp / "backlog.yaml").write_text(_HYP, encoding="utf-8")
    _seed(tasks, "HATS-1", state="brainstorm")
    _seed(hyp, "HYP-1")
    _seed(hyp, "HYP-2")
    root = RackRoot(project_dir=project, tasks_dir=tasks, prefix="HATS")
    return Workspace.discover([root]), root


def _link(kernel, owner, spec, cwd):
    return kernel.transition_ops(owner, parse_ops(["--link", spec]), actor="t", caller_cwd=cwd)


def test_cross_backlog_link_passes_existence(workspace, tmp_path):
    ws, root = workspace
    hyp = ws.kernel_for("HYP-1")
    res = _link(hyp, "HYP-1", "source_task:HATS-1", root.project_dir)
    assert res.ops[0]["changed"] is True
    assert hyp.get("HYP-1").links["source_task"] == ["HATS-1"]


def test_cross_backlog_missing_target_is_unknown_task(workspace):
    ws, root = workspace
    hyp = ws.kernel_for("HYP-1")
    with pytest.raises(UnknownTaskError) as err:
        _link(hyp, "HYP-1", "source_task:HATS-99", root.project_dir)
    assert err.value.task_id == "HATS-99"


def test_kind_without_targets_checks_own_catalog(workspace):
    ws, root = workspace
    hyp = ws.kernel_for("HYP-1")
    # `related` has no targets -> own (hypotheses) catalog; HYP-2 exists there.
    res = _link(hyp, "HYP-1", "related:HYP-2", root.project_dir)
    assert res.ops[0]["changed"] is True
    # a HATS id under a no-targets kind resolves to the HYP catalog and is absent.
    with pytest.raises(UnknownTaskError):
        _link(hyp, "HYP-1", "related:HATS-1", root.project_dir)


def test_unknown_prefix_target_is_not_found(workspace):
    ws, root = workspace
    hyp = ws.kernel_for("HYP-1")
    # source_task targets `tasks`; a foreign id there is simply absent.
    with pytest.raises(UnknownTaskError):
        _link(hyp, "HYP-1", "source_task:NOPE-1", root.project_dir)


def test_targets_parses_onto_the_kind(workspace):
    ws, _root = workspace
    hyp = next(i for i in ws.instances if i.name == "hypotheses")
    reg = hyp.definition.links_registry
    assert reg.get("source_task").targets == "tasks"
    assert reg.get("related").targets == ""
