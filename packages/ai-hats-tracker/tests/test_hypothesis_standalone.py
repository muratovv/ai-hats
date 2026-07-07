"""Standalone drive of the hypotheses/proposals half of ai-hats-tracker (HATS-935).

Proves a third party can import HypothesisStore/ProposalStore from ai_hats_tracker
alone and drive create / verdict / status / quorum-close on a bare tmp dir — no
ai_hats integrator, no ai-hats.yaml, no worktree.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import ai_hats_tracker as tracker
from ai_hats_tracker import (
    Hypothesis,
    HypothesisStore,
    Proposal,
    ProposalStore,
    ValidationLogEntry,
    Vote,
    next_hypothesis_id,
    next_proposal_id,
)
from ai_hats_tracker.hypothesis.quorum import autoclose_quorum

_HYP_SURFACE = {
    "Hypothesis",
    "HypothesisStore",
    "Proposal",
    "ProposalStore",
    "ValidationLogEntry",
    "Vote",
    "next_hypothesis_id",
    "next_proposal_id",
}

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _refuted(session: str) -> ValidationLogEntry:
    return ValidationLogEntry(
        date=date(2026, 1, 1),
        verdict="refuted",
        evidence=f"refuted by {session}",
        recommendation="close_refuted",
        session_id=session,
        timestamp=_TS,
    )


def test_hypothesis_surface_is_on_package_all() -> None:
    """The stores/models a standalone consumer drives are on ai_hats_tracker.__all__."""
    assert _HYP_SURFACE <= set(tracker.__all__), _HYP_SURFACE - set(tracker.__all__)


def test_hypothesis_store_crud_on_bare_dir(tmp_path: Path) -> None:
    store = HypothesisStore(tmp_path / "hypotheses")
    hid = next_hypothesis_id(store.dir)
    assert hid == "HYP-001"
    store.create(
        Hypothesis(
            id=hid,
            title="probe",
            status="active",
            created=date(2026, 1, 1),
            source_task="HATS-935",
            hypothesis="the store drives standalone",
        )
    )
    assert [h.id for h in store.list_all()] == [hid]
    store.append_verdict(hid, _refuted("sess-a"))
    assert store.load(hid).validation_log[0].verdict == "refuted"
    assert store.set_status(hid, "stalled").status == "stalled"


def test_proposal_store_crud_on_bare_dir(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals")
    pid = next_proposal_id(store.dir)
    assert pid == "PROP-001"
    store.create(
        Proposal(
            id=pid,
            created=_TS,
            title="p",
            category="code",
            target="x",
            description="d",
            rationale="r",
        )
    )
    voted = store.add_vote(pid, Vote(session_id="s1", timestamp=_TS, reasoning="ok"))
    assert len(voted.votes) == 1
    assert store.set_status(pid, "accepted").status == "accepted"
    assert [p.id for p in store.filter(status="accepted")] == [pid]


def test_quorum_autoclose_on_bare_dir(tmp_path: Path) -> None:
    store = HypothesisStore(tmp_path / "hypotheses")
    hid = next_hypothesis_id(store.dir)
    store.create(
        Hypothesis(
            id=hid,
            title="q",
            status="active",
            created=date(2026, 1, 1),
            source_task="HATS-935",
            hypothesis="quorum closes it",
        )
    )
    for s in ("sess-a", "sess-b", "sess-c"):
        store.append_verdict(hid, _refuted(s))
    closed = autoclose_quorum(store, 3)
    assert [c.hyp_id for c in closed] == [hid]
    assert store.load(hid).status == "refuted"
