"""HATS-1044 step 4: the independent-session quorum, ported to the rack side.

Every behaviour pinned in ``packages/ai-hats-tracker/tests/test_quorum.py`` gets
an equivalent here against the ``hyp-verdicts`` autoclose API + the
``hyp-quorum-gate`` edge handler on the PACKAGED hypotheses definition — K default,
distinct/duplicate/verdict/exclusion counting, only-active scan, append-then-flip
atomicity, skip-if-not-active, idempotency, custom-k, at-or-above. Plus the
rack-specific gate rule: auto-close is gated on the automation actor, a manual
refute never is (ADR-0009 safe direction).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats_rack.cardschema import build_card_schema
from ai_hats_rack.composition import compose_subscribers, stock_factories, stock_validators
from ai_hats_rack.definition import load_packaged_definition, packaged_definition_source
from ai_hats_rack.dispatch import OperationAborted, bind_subscribers
from ai_hats_rack.extensions.quorum import AUTO_SESSION_ID, AUTOCLOSE_ACTOR, DEFAULT_QUORUM_K
from ai_hats_rack.kernel import Kernel
from ai_hats_rack.ops import StateOp
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import Workspace

NOW = "2026-06-15T12:00:00Z"


def _hyp(catalog: Path):
    defn = load_packaged_definition("hypotheses")
    subs = compose_subscribers(defn, catalog, stock_factories())
    kernel = Kernel(
        catalog,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        edge_names=defn.edge_names,
        schema=build_card_schema(defn, stock_validators()),
        subscribers=subs,
    )
    bind_subscribers(subs, kernel)
    return kernel, next(s for s in subs if s.name == "hyp-verdicts")


def _refuted(session_id: str | None) -> dict:
    e = {"date": "2026-06-10", "verdict": "refuted", "evidence": "behaviour gone"}
    if session_id is not None:
        e["session_id"] = session_id
    return e


def _seed(catalog: Path, hyp_id: str, *, status: str = "active", log=None) -> None:
    d = catalog / hyp_id
    d.mkdir(parents=True, exist_ok=True)
    body = {"id": hyp_id, "title": f"t-{hyp_id}", "state": status, "hypothesis": "h",
            "validation_log": log or []}
    (d / "task.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


# ----- pure counting: K boundary, distinct, verdict filter, exclusions -------


def test_default_k_is_three():
    assert DEFAULT_QUORUM_K == 3


def test_three_distinct_sessions_reach_quorum(tmp_path):
    kernel, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    (closure,) = v.find_quorum_closures()
    assert closure.hyp_id == "HYP-001"
    assert closure.refute_sessions == ("s1", "s2", "s3")


def test_two_distinct_sessions_below_quorum(tmp_path):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2")])
    assert v.find_quorum_closures() == []


def test_duplicate_session_counts_once(tmp_path):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s1"), _refuted("s1")])
    assert v.find_quorum_closures() == []


def test_confirmed_and_inconclusive_verdicts_ignored(tmp_path):
    _, v = _hyp(tmp_path)
    log = [
        {"date": "2026-06-10", "verdict": "confirmed", "evidence": "x", "session_id": "s1"},
        {"date": "2026-06-10", "verdict": "inconclusive", "evidence": "x", "session_id": "s2"},
        _refuted("s3"),
    ]
    _seed(tmp_path, "HYP-001", log=log)
    assert v.find_quorum_closures() == []


def test_entry_without_session_excluded(tmp_path):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted(None), _refuted("s1"), _refuted("s2")])
    assert v.find_quorum_closures() == []


def test_sentinel_session_excluded(tmp_path):
    """The synthetic auto-quorum entry must not count toward a future quorum."""
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted(AUTO_SESSION_ID)])
    assert v.find_quorum_closures() == []


def test_only_active_hyps_scanned(tmp_path):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", status="refuted",
          log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    assert v.find_quorum_closures() == []


def test_custom_k_threshold(tmp_path):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2")])
    assert v.find_quorum_closures(k=2) and not v.find_quorum_closures(k=3)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_quorum_met_at_or_above_threshold(tmp_path, count):
    _, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted(f"s{i}") for i in range(count)])
    assert len(v.find_quorum_closures()) == 1


# ----- append-then-flip atomicity, skip-if-not-active, idempotency -----------


def test_autoclose_appends_audit_then_flips(tmp_path):
    kernel, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])

    (closed,) = v.autoclose(caller_cwd=tmp_path, now=NOW)
    assert closed.hyp_id == "HYP-001"

    card = kernel.get("HYP-001")
    assert card.state == "refuted"
    last = card.extras["validation_log"][-1]
    assert last["verdict"] == "refuted"
    assert last["recommendation"] == "close_refuted"
    assert last["session_id"] == AUTO_SESSION_ID
    assert "K=3" in last["evidence"] and "s1, s2, s3" in last["evidence"]
    assert last["timestamp"] == NOW
    assert card.extras["closed"]  # stamp-lifecycle rode the same transaction


def test_autoclose_skips_when_not_active(tmp_path):
    """The FSM guard refuses to refute (and to append to) a non-active HYP."""
    kernel, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", status="confirmed",
          log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])

    out = v.append_then_set_status(
        "HYP-001", _refuted(AUTO_SESSION_ID), to_state="refuted",
        actor=AUTOCLOSE_ACTOR, caller_cwd=tmp_path,
    )
    assert out is None
    card = kernel.get("HYP-001")
    assert card.state == "confirmed"
    assert all(e["session_id"] != AUTO_SESSION_ID for e in card.extras["validation_log"])


def test_autoclose_is_idempotent(tmp_path):
    kernel, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])

    first = v.autoclose(caller_cwd=tmp_path, now=NOW)
    assert [c.hyp_id for c in first] == ["HYP-001"]

    second = v.autoclose(caller_cwd=tmp_path, now=NOW)  # no longer active → no-op
    assert second == []
    card = kernel.get("HYP-001")
    assert sum(1 for e in card.extras["validation_log"] if e["session_id"] == AUTO_SESSION_ID) == 1


def test_autoclose_dry_run_writes_nothing(tmp_path):
    kernel, v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    closures = v.autoclose(caller_cwd=tmp_path, dry_run=True, now=NOW)
    assert [c.hyp_id for c in closures] == ["HYP-001"]
    card = kernel.get("HYP-001")
    assert card.state == "active"  # unchanged
    assert all(e["session_id"] != AUTO_SESSION_ID for e in card.extras["validation_log"])


# ----- the gate: auto-close gated, manual refute never (ADR-0009) ------------


def test_gate_blocks_automation_actor_without_quorum(tmp_path):
    kernel, _v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2")])  # below K
    with pytest.raises(OperationAborted) as err:
        kernel.transition_ops("HYP-001", [StateOp("refuted")], actor=AUTOCLOSE_ACTOR, caller_cwd=tmp_path)
    assert "quorum" in err.value.reason
    assert kernel.get("HYP-001").state == "active"  # aborted before persist


def test_gate_passes_automation_actor_at_quorum(tmp_path):
    kernel, _v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    kernel.transition_ops("HYP-001", [StateOp("refuted")], actor=AUTOCLOSE_ACTOR, caller_cwd=tmp_path)
    assert kernel.get("HYP-001").state == "refuted"


def test_gate_never_blocks_a_manual_refute(tmp_path):
    """A HITL refute passes with no quorum — quorum licenses auto-close only."""
    kernel, _v = _hyp(tmp_path)
    _seed(tmp_path, "HYP-001", log=[])  # zero refuted sessions
    kernel.transition_ops("HYP-001", [StateOp("refuted")], actor="user", caller_cwd=tmp_path)
    assert kernel.get("HYP-001").state == "refuted"


# ----- reach: the sweep via Workspace.extension (threading pin) --------------


def test_autoclose_reached_through_workspace_extension(tmp_path):
    project = tmp_path / "proj"
    tasks = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    hyp = project / ".agent" / "ai-hats" / "tracker" / "hypotheses"
    hyp.mkdir(parents=True)
    (hyp / "backlog.yaml").write_text(packaged_definition_source("hypotheses"), encoding="utf-8")
    _seed(hyp, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    ws = Workspace.discover([RackRoot(project_dir=project, tasks_dir=tasks, prefix="HATS")])

    closed = ws.extension("hyp-verdicts").autoclose(caller_cwd=project, now=NOW)
    assert [c.hyp_id for c in closed] == ["HYP-001"]
    assert ws.kernel_for("HYP-001").get("HYP-001").state == "refuted"
