"""Atomic IO + filelock tests for HypothesisStore and ProposalStore."""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from ai_hats_tracker.hypothesis import (
    HypothesisStore,
    Proposal,
    ProposalStore,
    ValidationLogEntry,
    Vote,
    next_proposal_id,
)


def _make_hyp_yaml(tmp: Path, hyp_id: str = "HYP-001", **extra) -> Path:
    body = {
        "id": hyp_id,
        "title": "t",
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [],
        **extra,
    }
    p = tmp / f"{hyp_id}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


class TestHypothesisStore:
    def test_load_and_list(self, tmp_path: Path):
        d = tmp_path / "hypotheses"
        d.mkdir()
        _make_hyp_yaml(d, "HYP-001")
        _make_hyp_yaml(d, "HYP-002", status="confirmed")
        store = HypothesisStore(d)
        all_h = store.list_all()
        assert {h.id for h in all_h} == {"HYP-001", "HYP-002"}
        active = store.list_active()
        assert [h.id for h in active] == ["HYP-001"]

    def test_path_lookup_by_id_with_slug_filename(self, tmp_path: Path):
        d = tmp_path / "hypotheses"
        d.mkdir()
        p = d / "HYP-008-bash-anti-patterns.yaml"
        p.write_text(yaml.safe_dump({
            "id": "HYP-008",
            "title": "t",
            "status": "active",
            "created": "2026-05-03",
            "source_task": "HATS-209",
            "hypothesis": "h",
        }))
        store = HypothesisStore(d)
        assert store.path("HYP-008") == p

    def test_append_verdict_preserves_extras(self, tmp_path: Path):
        d = tmp_path / "hypotheses"
        d.mkdir()
        p = d / "HYP-001.yaml"
        p.write_text(yaml.safe_dump({
            "id": "HYP-001",
            "title": "t",
            "status": "active",
            "created": "2026-01-01",
            "source_task": "HATS-001",
            "hypothesis": "h",
            "validation_log": [],
            "legacy_top_level": "preserved",
        }))
        store = HypothesisStore(d)
        entry = ValidationLogEntry(
            date=date(2026, 5, 4),
            verdict="confirmed",
            evidence="metric X dropped to 0",
            recommendation="close_confirmed",
            session_id="s1",
        )
        h = store.append_verdict("HYP-001", entry)
        assert len(h.validation_log) == 1
        # disk roundtrip preserves the legacy key
        raw = yaml.safe_load(p.read_text())
        assert raw["legacy_top_level"] == "preserved"
        assert raw["validation_log"][0]["verdict"] == "confirmed"

    def test_concurrent_append_no_lost_writes(self, tmp_path: Path):
        d = tmp_path / "hypotheses"
        d.mkdir()
        _make_hyp_yaml(d, "HYP-001")
        store = HypothesisStore(d)
        N = 20
        errors: list[Exception] = []

        def worker(i: int):
            try:
                store.append_verdict(
                    "HYP-001",
                    ValidationLogEntry(
                        date=date(2026, 5, 4),
                        verdict="inconclusive",
                        evidence=f"e{i}",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        h = store.load("HYP-001")
        assert len(h.validation_log) == N
        evidences = sorted(e.evidence for e in h.validation_log)
        assert evidences == sorted(f"e{i}" for i in range(N))


class TestProposalStore:
    def _proposal(self, pid="PROP-001", **kw) -> Proposal:
        defaults = dict(
            id=pid,
            created=datetime(2026, 5, 4, tzinfo=timezone.utc),
            title="t",
            category="rule",
            target="dev_rule_x",
            description="d",
            rationale="r",
        )
        defaults.update(kw)
        return Proposal(**defaults)

    def test_create_and_load(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        loaded = store.load("PROP-001")
        assert loaded.id == "PROP-001"
        assert loaded.status == "open"

    def test_create_duplicate_raises(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        try:
            store.create(self._proposal("PROP-001"))
        except FileExistsError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected FileExistsError")

    def test_add_vote_atomic(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        v = Vote(
            session_id="s1",
            timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc),
            reasoning="agree",
        )
        p = store.add_vote("PROP-001", v)
        assert len(p.votes) == 1
        assert p.votes[0].reasoning == "agree"

    def test_concurrent_votes_no_loss(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        N = 15
        errors: list[Exception] = []

        def worker(i: int):
            try:
                store.add_vote(
                    "PROP-001",
                    Vote(
                        session_id=f"s{i}",
                        timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc),
                        reasoning=f"r{i}",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        p = store.load("PROP-001")
        assert len(p.votes) == N

    def test_set_status(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        p = store.set_status("PROP-001", "accepted")
        assert p.status == "accepted"

    def test_filter(self, tmp_path: Path):
        d = tmp_path / "proposals"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        store.create(self._proposal("PROP-002", category="code"))
        store.set_status("PROP-001", "accepted")
        assert [p.id for p in store.filter(status="open")] == ["PROP-002"]
        assert [p.id for p in store.filter(category="rule")] == ["PROP-001"]

    def test_next_proposal_id(self, tmp_path: Path):
        d = tmp_path / "proposals"
        assert next_proposal_id(d) == "PROP-001"
        store = ProposalStore(d)
        store.create(self._proposal("PROP-001"))
        store.create(self._proposal("PROP-005"))
        assert next_proposal_id(d) == "PROP-006"
