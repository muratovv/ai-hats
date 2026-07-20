"""HATS-1044 step 1: the multi-backlog workspace resolver (ADR-0017 §2).

discover finds the tasks catalog (always) plus sibling backlog.yaml files under
tracker; routing is by id prefix (unknown -> typed, cross-root duplicate ->
AmbiguousPrefixError, within-root duplicate -> DuplicatePrefixError at load);
kernel_for uses the integrator override for the tasks instance and the portable
kit for siblings; a tasks-only repo yields exactly one instance (zero change).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.kernel import Kernel
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import (
    AmbiguousPrefixError,
    DuplicatePrefixError,
    UnknownPrefixError,
    Workspace,
)

_HYP = """\
name: hypotheses
prefix: HYP
fsm:
  initial: active
  states: [{name: active}, {name: confirmed}]
  edges: [{from: active, to: confirmed}]
links:
  kinds:
    - {name: source_task, arity: one}
"""

_TASKS_TAIL = Path("tracker") / "backlog" / "tasks"


def _root(tmp_path: Path, *, prefix: str = "HATS", name: str = "proj") -> RackRoot:
    project = tmp_path / name
    tasks = project / ".agent" / "ai-hats" / _TASKS_TAIL
    tasks.mkdir(parents=True, exist_ok=True)
    return RackRoot(project_dir=project, tasks_dir=tasks, prefix=prefix)


def _mount_hyp(root: RackRoot, backlog: str = _HYP) -> Path:
    catalog = root.tasks_dir.parent.parent / "hypotheses"
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "backlog.yaml").write_text(backlog, encoding="utf-8")
    return catalog


def _card(catalog: Path, task_id: str) -> None:
    d = catalog / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.yaml").write_text(f"id: {task_id}\nstate: active\n", encoding="utf-8")


# ----- discovery -------------------------------------------------------------


def test_tasks_only_repo_is_exactly_one_instance(tmp_path):
    ws = Workspace.discover([_root(tmp_path)])
    assert len(ws.instances) == 1
    (only,) = ws.instances
    assert only.name == "tasks" and only.prefix == "HATS" and only.is_tasks


def test_discover_finds_sibling_backlog(tmp_path):
    root = _root(tmp_path)
    _mount_hyp(root)
    ws = Workspace.discover([root])
    names = sorted(i.name for i in ws.instances)
    assert names == ["hypotheses", "tasks"]
    hyp = next(i for i in ws.instances if i.name == "hypotheses")
    assert hyp.prefix == "HYP" and not hyp.is_tasks


def test_task_prefix_alias_applies_to_tasks_instance(tmp_path):
    # A catalog without backlog.yaml is the packaged default; the deprecated
    # ai-hats.yaml task_prefix alias (root.prefix) names its instance.
    ws = Workspace.discover([_root(tmp_path, prefix="SBX")])
    assert ws.instances[0].prefix == "SBX"


def test_override_tasks_dir_does_not_scan(tmp_path):
    # A non-conventional --tasks-dir override never walks an arbitrary tree.
    tasks = tmp_path / "somewhere" / "tasks"
    tasks.mkdir(parents=True)
    root = RackRoot(project_dir=tmp_path, tasks_dir=tasks, prefix="HATS")
    ws = Workspace.discover([root])
    assert len(ws.instances) == 1  # only the tasks instance; no sibling scan


# ----- routing ---------------------------------------------------------------


def test_instance_for_routes_by_prefix(tmp_path):
    root = _root(tmp_path)
    _mount_hyp(root)
    ws = Workspace.discover([root])
    assert ws.instance_for("HYP-42").name == "hypotheses"
    assert ws.instance_for("HATS-1").name == "tasks"


def test_unknown_prefix_names_the_configured_set(tmp_path):
    ws = Workspace.discover([_root(tmp_path)])
    with pytest.raises(UnknownPrefixError) as err:
        ws.instance_for("NOPE-1")
    assert err.value.prefix == "NOPE"
    assert err.value.configured == ("HATS",)


def test_within_root_duplicate_prefix_fails_closed(tmp_path):
    root = _root(tmp_path)
    # a sibling backlog re-using the tasks prefix HATS is a load-time refusal.
    _mount_hyp(root, backlog=_HYP.replace("prefix: HYP", "prefix: HATS"))
    with pytest.raises(DuplicatePrefixError) as err:
        Workspace.discover([root])
    assert err.value.prefix == "HATS"


def test_cross_root_duplicate_prefix_is_ambiguous(tmp_path):
    a = _root(tmp_path, name="a")
    b = _root(tmp_path, name="b")  # both default to HATS
    ws = Workspace.discover([a, b])
    with pytest.raises(AmbiguousPrefixError) as err:
        ws.instance_for("HATS-1")
    assert err.value.prefix == "HATS"
    assert sorted(err.value.roots) == ["a", "b"]
    # the qualifier disambiguates — never a silent first-match.
    assert ws.instance_for("a:HATS-1").root_id == "a"
    assert ws.instance_for("HATS-1", root="b").root_id == "b"


# ----- kernel_for + exists ---------------------------------------------------


def test_kernel_for_tasks_uses_the_builder_override(tmp_path):
    root = _root(tmp_path)
    sentinel = object()
    ws = Workspace.discover([root], kernel_builder=lambda inst: sentinel if inst.is_tasks else None)
    assert ws.kernel_for("HATS-1") is sentinel


def test_kernel_for_sibling_builds_the_portable_kit(tmp_path):
    root = _root(tmp_path)
    _mount_hyp(root)
    ws = Workspace.discover([root], kernel_builder=lambda inst: None)
    kernel = ws.kernel_for("HYP-7")
    assert isinstance(kernel, Kernel)
    assert kernel.prefix == "HYP"
    assert kernel.topology.initial == "active"


def test_exists_is_cross_backlog(tmp_path):
    root = _root(tmp_path)
    hyp = _mount_hyp(root)
    _card(hyp, "HYP-1")
    _card(root.tasks_dir, "HATS-1")
    ws = Workspace.discover([root])
    assert ws.exists("HYP-1") is True
    assert ws.exists("HATS-1") is True
    assert ws.exists("HYP-99") is False  # configured prefix, absent card
    assert ws.exists("NOPE-1") is False  # unknown prefix -> not-found, not a raise
