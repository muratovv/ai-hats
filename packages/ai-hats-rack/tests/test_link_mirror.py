"""HATS-1044 step 3: link mirror events + the stock ``mirror-link`` reaction
(ADR-0017 §2/R4).

After the origin link persists, the workspace dispatches ``link-target:<inverse>``
to the TARGET backlog's kernel, where the mirror-link reaction repairs the reverse
edge in a fresh lock window (sequential, never nested). It is convergent
(idempotent repair), fail-soft (a reaction failure is journaled, never touches the
origin), and load-time fail-closed (a stored inverse pair MUST declare it). The
packaged tasks default declares no mirror kinds and loads unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.composition import build_link_subscribers, compose_subscribers, stock_factories
from ai_hats_rack.definition import MissingMirrorReactionError, load_backlog
from ai_hats_rack.dispatch import Phase
from ai_hats_rack.events import LinkMirrorEvent
from ai_hats_rack.models import TaskCard
from ai_hats_rack.ops import parse_ops
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import Workspace

from rack_testkit import make_kernel

_TAIL = Path("tracker") / "backlog" / "tasks"


def _pair(name: str, prefix: str, targets: str) -> str:
    return f"""\
name: {name}
prefix: {prefix}
fsm:
  initial: open
  states: [{{name: open}}]
  edges: []
links:
  kinds:
    - {{name: mirror_to, arity: many, targets: {targets}, inverse: mirror_from, handlers: [mirror-link]}}
    - {{name: mirror_from, arity: many, targets: {targets}, inverse: mirror_to, handlers: [mirror-link]}}
"""


def _seed(catalog: Path, task_id: str, **fields) -> None:
    d = catalog / task_id
    d.mkdir(parents=True, exist_ok=True)
    TaskCard(id=task_id, state="open", **fields).save(d / "task.yaml")


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "proj"
    tasks = project / ".agent" / "ai-hats" / _TAIL
    tasks.mkdir(parents=True)
    tracker = project / ".agent" / "ai-hats" / "tracker"
    alpha = tracker / "alpha"
    beta = tracker / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / "backlog.yaml").write_text(_pair("alpha", "AA", "beta"), encoding="utf-8")
    (beta / "backlog.yaml").write_text(_pair("beta", "BB", "alpha"), encoding="utf-8")
    _seed(alpha, "AA-1")
    _seed(beta, "BB-1")
    root = RackRoot(project_dir=project, tasks_dir=tasks, prefix="HATS")
    return Workspace.discover([root]), project, alpha, beta


# ----- cross-backlog mirror fires on the target kernel post-lock -------------


def test_cross_backlog_mirror_fires_on_target_kernel(project):
    ws, cwd, _alpha, _beta = project
    a = ws.kernel_for("AA-1")
    res = a.transition_ops("AA-1", parse_ops(["--link", "mirror_to:BB-1"]), actor="t", caller_cwd=cwd)
    assert res.ops[0]["changed"] is True
    assert ws.kernel_for("BB-1").get("BB-1").links.get("mirror_from") in (None, [])  # not yet
    ws.mirror_after("AA-1", res, actor="t", caller_cwd=cwd)  # post-lock dispatch
    assert ws.kernel_for("BB-1").get("BB-1").links["mirror_from"] == ["AA-1"]


def test_mirror_unlink_removes_the_reverse_edge(project):
    ws, proj, alpha, beta = project
    _seed(beta, "BB-1", links={"mirror_from": ["AA-1"]})  # reverse already present
    _seed(alpha, "AA-1", links={"mirror_to": ["BB-1"]})  # forward present
    a = ws.kernel_for("AA-1")
    res = a.transition_ops("AA-1", parse_ops(["--unlink", "mirror_to:BB-1"]), actor="t", caller_cwd=proj)
    assert res.ops[0]["changed"] is True
    ws.mirror_after("AA-1", res, actor="t", caller_cwd=proj)
    assert ws.kernel_for("BB-1").get("BB-1").links.get("mirror_from") in (None, [])


# ----- broken inverse pair repaired idempotently -----------------------------


def test_broken_inverse_pair_repaired_idempotently(project):
    ws, cwd, _alpha, _beta = project
    event = LinkMirrorEvent(kind="mirror_from", origin="AA-1", target="BB-1")
    rec = ws.dispatch_mirror(event, actor="t", caller_cwd=cwd)
    assert rec.result == "persisted"
    assert ws.kernel_for("BB-1").get("BB-1").links["mirror_from"] == ["AA-1"]
    # a second convergent run repairs nothing and never duplicates
    ws.dispatch_mirror(event, actor="t", caller_cwd=cwd)
    assert ws.kernel_for("BB-1").get("BB-1").links["mirror_from"] == ["AA-1"]


# ----- mirror failure journaled without affecting origin ---------------------


class _Boom:
    name = "boom"
    MIRROR = True
    PHASE = Phase.POST_LOCK

    def on_event(self, ctx):
        raise RuntimeError("mirror blew up")


def test_mirror_failure_is_journaled_and_origin_untouched(tmp_path):
    catalog = tmp_path / "cat"
    doc = catalog / "backlog.yaml"
    catalog.mkdir(parents=True)
    doc.write_text(
        "name: boomy\nprefix: T\n"
        "fsm:\n  initial: open\n  states: [{name: open}]\n  edges: []\n"
        "links:\n  kinds:\n    - {name: reverse, arity: many, handlers: [boom]}\n",
        encoding="utf-8",
    )
    defn = load_backlog(doc)
    subs = compose_subscribers(defn, catalog, {**stock_factories(), "boom": lambda d, c, cfg: _Boom()})
    kernel = make_kernel(catalog, topology=defn.topology, registry=defn.links_registry, subscribers=subs)
    _seed(catalog, "T-1")
    rec = kernel.apply_mirror(
        LinkMirrorEvent(kind="reverse", origin="X-1", target="T-1"), actor="t", caller_cwd=tmp_path
    )
    assert rec.result == "aborted"  # fail-soft: the reaction blew up
    assert any(o.outcome == "error" for o in rec.outcomes)
    assert kernel.get("T-1").links.get("reverse") in (None, [])  # target unchanged


# ----- fail-closed on stored-inverse-without-mirror declaration --------------


def test_stored_inverse_without_mirror_fails_closed(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: h\nprefix: H\n"
        "fsm:\n  initial: open\n  states: [{name: open}]\n  edges: []\n"
        "links:\n  kinds:\n"
        "    - {name: supersedes, arity: one, inverse: superseded_by}\n"
        "    - {name: superseded_by, arity: one, inverse: supersedes}\n"
    )
    with pytest.raises(MissingMirrorReactionError) as err:
        load_backlog(doc)
    assert err.value.kind in ("supersedes", "superseded_by")


def test_symmetric_and_derived_inverse_are_exempt(tmp_path):
    # related (symmetric) + parent_task/children (derived inverse) need no mirror.
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: t\nprefix: T\n"
        "fsm:\n  initial: open\n  states: [{name: open}]\n  edges: []\n"
        "links:\n  kinds:\n"
        "    - {name: parent_task, arity: one, inverse: children}\n"
        "    - {name: children, derived: true, inverse: parent_task}\n"
        "    - {name: related, arity: many, inverse: related}\n"
    )
    assert load_backlog(doc).links_registry.get("related").symmetric is True  # no raise


# ----- composition: mirror handlers subscribe the target-side keys -----------


def test_mirror_handler_subscribes_link_target_keys(project):
    ws, _cwd, _alpha, _beta = project
    alpha = next(i for i in ws.instances if i.name == "alpha")
    subs = build_link_subscribers(alpha.definition, alpha.catalog, stock_factories())
    mirror = [s for s in subs if s.name == "mirror-link"]
    assert len(mirror) == 1  # one channel for both kinds (grouped by name+config)
    keys = sorted(sub.event_key for sub in mirror[0].subscriptions())
    assert keys == [
        "link-target:mirror_from",
        "link-target:mirror_to",
        "unlink-target:mirror_from",
        "unlink-target:mirror_to",
    ]
    assert not any(k.startswith("link:") for k in keys)  # never the owning-side keys


# ----- packaged tasks default loads unchanged --------------------------------


def test_packaged_tasks_default_loads_with_no_mirror(tmp_path):
    defn = load_backlog()  # no MissingMirrorReactionError (related/children exempt)
    subs = compose_subscribers(defn, tmp_path, stock_factories())
    keys = [sub.event_key for s in subs for sub in s.subscriptions()]
    assert not any(k.startswith("link-target:") for k in keys)
