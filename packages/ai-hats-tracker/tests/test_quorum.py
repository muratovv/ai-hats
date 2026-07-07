"""Unit tests for the quorum safe-close core (HATS-769).

Pure-core coverage of ``hypothesis.quorum``: independence counting, the K
boundary, the verdict filter, the two exclusion rules (no-session / sentinel),
the audit-entry-then-flip closure, and idempotency. Deterministic ``now`` is
injected so the audit timestamp is stable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ai_hats_tracker.hypothesis import HypothesisStore
from ai_hats_tracker.hypothesis.quorum import (
    AUTO_SESSION_ID,
    DEFAULT_QUORUM_K,
    QuorumClosure,
    apply_closure,
    autoclose_quorum,
    find_quorum_closures,
)

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> HypothesisStore:
    d = tmp_path / "hyp"
    d.mkdir()
    return HypothesisStore(d)


def _refuted(session_id: str | None) -> dict:
    e = {"date": "2026-06-10", "verdict": "refuted", "evidence": "behaviour gone"}
    if session_id is not None:
        e["session_id"] = session_id
    return e


def _write(store: HypothesisStore, hyp_id: str, *, status: str = "active", log=None) -> None:
    body = {
        "id": hyp_id,
        "title": f"t-{hyp_id}",
        "status": status,
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": log or [],
    }
    (store.dir / f"{hyp_id}.yaml").write_text(yaml.safe_dump(body))


def test_default_k_is_three():
    assert DEFAULT_QUORUM_K == 3


def test_three_distinct_sessions_reach_quorum(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    closures = find_quorum_closures(store)
    assert len(closures) == 1
    assert closures[0].hyp_id == "HYP-001"
    assert closures[0].refute_sessions == ("s1", "s2", "s3")


def test_two_distinct_sessions_below_quorum(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s2")])
    assert find_quorum_closures(store) == []


def test_duplicate_session_counts_once(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s1"), _refuted("s1")])
    assert find_quorum_closures(store) == []


def test_confirmed_and_inconclusive_verdicts_ignored(tmp_path: Path):
    store = _store(tmp_path)
    log = [
        {"date": "2026-06-10", "verdict": "confirmed", "evidence": "x", "session_id": "s1"},
        {"date": "2026-06-10", "verdict": "inconclusive", "evidence": "x", "session_id": "s2"},
        _refuted("s3"),
    ]
    _write(store, "HYP-001", log=log)
    assert find_quorum_closures(store) == []


def test_entry_without_session_excluded(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted(None), _refuted("s1"), _refuted("s2")])
    assert find_quorum_closures(store) == []


def test_sentinel_session_excluded(tmp_path: Path):
    """The synthetic auto-quorum entry must not count toward a future quorum."""
    store = _store(tmp_path)
    _write(
        store,
        "HYP-001",
        log=[_refuted("s1"), _refuted("s2"), _refuted(AUTO_SESSION_ID)],
    )
    assert find_quorum_closures(store) == []


def test_only_active_hyps_scanned(tmp_path: Path):
    store = _store(tmp_path)
    _write(
        store,
        "HYP-001",
        status="refuted",
        log=[_refuted("s1"), _refuted("s2"), _refuted("s3")],
    )
    assert find_quorum_closures(store) == []


def test_apply_closure_appends_audit_then_flips(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])
    (closure,) = find_quorum_closures(store)

    h = apply_closure(store, closure, now=NOW)

    assert h.status == "refuted"
    last = h.validation_log[-1]
    assert last.verdict == "refuted"
    assert last.recommendation == "close_refuted"
    assert last.session_id == AUTO_SESSION_ID
    assert "K=3" in last.evidence
    assert "s1, s2, s3" in last.evidence
    assert last.timestamp == NOW


def test_apply_closure_skips_when_not_active(tmp_path: Path):
    """The atomic guard refuses to close (and to append) a non-active HYP.

    This is the concurrency/repeat-close guard: a closer that loses the race
    finds the HYP already non-active under the lock and makes no change.
    """
    store = _store(tmp_path)
    _write(
        store,
        "HYP-001",
        status="confirmed",
        log=[_refuted("s1"), _refuted("s2"), _refuted("s3")],
    )
    closure = QuorumClosure("HYP-001", ("s1", "s2", "s3"), 3)

    assert apply_closure(store, closure, now=NOW) is None

    h = store.load("HYP-001")
    assert h.status == "confirmed"
    assert all(e.session_id != AUTO_SESSION_ID for e in h.validation_log)


def test_autoclose_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s2"), _refuted("s3")])

    first = autoclose_quorum(store, now=NOW)
    assert [c.hyp_id for c in first] == ["HYP-001"]

    # Re-running must be a no-op: the HYP is no longer active, and the synthetic
    # entry is excluded — so no re-close and no second audit entry.
    second = autoclose_quorum(store, now=NOW)
    assert second == []
    h = store.load("HYP-001")
    assert sum(1 for e in h.validation_log if e.session_id == AUTO_SESSION_ID) == 1


def test_custom_k_threshold(tmp_path: Path):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted("s1"), _refuted("s2")])
    assert find_quorum_closures(store, k=2) and not find_quorum_closures(store, k=3)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_quorum_met_at_or_above_threshold(tmp_path: Path, count: int):
    store = _store(tmp_path)
    _write(store, "HYP-001", log=[_refuted(f"s{i}") for i in range(count)])
    assert len(find_quorum_closures(store)) == 1
