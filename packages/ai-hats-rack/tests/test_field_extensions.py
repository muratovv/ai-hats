"""HATS-1044 step 4: the field-owning extension APIs step-6 consumers code against.

``hyp-verdicts`` (append_verdict / append_then_set_status) and ``prop-votes``
(add_vote) both write their owned field via ``Delta.fields`` on the composite-
transition path — an append is atomic, and an append can ride a state change in
ONE lock/persist (the tracker append-then-set-status contract). The real stock
validators gate each write.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats_rack.cardschema import FieldValidationError, build_card_schema
from ai_hats_rack.composition import compose_subscribers, stock_factories, stock_validators
from ai_hats_rack.definition import load_packaged_definition
from ai_hats_rack.dispatch import bind_subscribers
from ai_hats_rack.kernel import Kernel


def _kernel(catalog: Path, name: str):
    defn = load_packaged_definition(name)
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
    return kernel, {s.name: s for s in subs}


def _seed(catalog: Path, task_id: str, *, state: str, **body) -> None:
    d = catalog / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.yaml").write_text(
        yaml.safe_dump({"id": task_id, "title": "t", "state": state, **body}, sort_keys=False),
        encoding="utf-8",
    )


# ----- hyp-verdicts: append-only + append-then-transition atomicity ----------


def test_append_verdict_appends_without_state_change(tmp_path, cwd):
    kernel, exts = _kernel(tmp_path, "hypotheses")
    _seed(tmp_path, "HYP-1", state="active", hypothesis="h", validation_log=[])
    entry = {"date": "2026-06-10", "verdict": "inconclusive", "evidence": "seen", "session_id": "s1"}
    res = exts["hyp-verdicts"].append_verdict("HYP-1", entry, actor="reflect", caller_cwd=cwd)
    card = kernel.get("HYP-1")
    assert card.state == "active"  # no transition
    assert card.extras["validation_log"] == [entry]
    assert [o["op"] for o in res.ops] == ["fields"]


def test_append_then_set_status_rides_transition_atomically(tmp_path, cwd):
    kernel, exts = _kernel(tmp_path, "hypotheses")
    _seed(tmp_path, "HYP-1", state="active", hypothesis="h", validation_log=[])
    entry = {"date": "2026-06-10", "verdict": "confirmed", "evidence": "held", "session_id": "s1"}
    res = exts["hyp-verdicts"].append_then_set_status(
        "HYP-1", entry, to_state="confirmed", actor="reflect", caller_cwd=cwd
    )
    card = kernel.get("HYP-1")
    assert card.state == "confirmed"  # transition rode the same lock
    assert card.extras["validation_log"] == [entry]
    assert card.extras["closed"]  # stamp-lifecycle stamped in the same persist
    assert res is not None


def test_append_verdict_malformed_entry_is_refused_atomically(tmp_path, cwd):
    kernel, exts = _kernel(tmp_path, "hypotheses")
    _seed(tmp_path, "HYP-1", state="active", hypothesis="h", validation_log=[])
    bad = {"verdict": "refuted"}  # missing required evidence/date
    with pytest.raises(FieldValidationError):
        exts["hyp-verdicts"].append_verdict("HYP-1", bad, actor="reflect", caller_cwd=cwd)
    assert kernel.get("HYP-1").extras.get("validation_log") in (None, [])  # nothing persisted


# ----- prop-votes: append a vote atomically ----------------------------------


def test_add_vote_appends_a_valid_vote(tmp_path, cwd):
    kernel, exts = _kernel(tmp_path, "proposals")
    _seed(tmp_path, "PROP-1", state="open", category="rule", target="x",
          description="d", rationale="r")
    vote = {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z", "reasoning": "sound"}
    exts["prop-votes"].add_vote("PROP-1", vote, actor="reflect", caller_cwd=cwd)
    assert kernel.get("PROP-1").extras["votes"] == [vote]


def test_add_vote_rejects_a_malformed_vote_atomically(tmp_path, cwd):
    kernel, exts = _kernel(tmp_path, "proposals")
    _seed(tmp_path, "PROP-1", state="open", category="rule", target="x",
          description="d", rationale="r")
    bad = {"session_id": "s1"}  # missing reasoning/timestamp (Vote extra=forbid shape)
    with pytest.raises(FieldValidationError):
        exts["prop-votes"].add_vote("PROP-1", bad, actor="reflect", caller_cwd=cwd)
    assert kernel.get("PROP-1").extras.get("votes") in (None, [])
