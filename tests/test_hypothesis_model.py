"""Unit tests for hypothesis model + proposal model."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from ai_hats.hypothesis import (
    ExitCriteria,
    Hypothesis,
    Proposal,
    ValidationLogEntry,
    Vote,
)


class TestHypothesis:
    def test_minimal_active_hypothesis(self):
        h = Hypothesis(
            id="HYP-001",
            title="t",
            status="active",
            created=date(2026, 1, 1),
            source_task="HATS-001",
            hypothesis="rule X reduces Y",
        )
        assert h.id == "HYP-001"
        assert h.min_sessions_per_bundle == 4
        assert h.validation_log == []

    def test_id_pattern_enforced(self):
        with pytest.raises(ValidationError):
            Hypothesis(
                id="bad",
                title="t",
                status="active",
                created=date(2026, 1, 1),
                source_task="HATS-001",
                hypothesis="h",
            )

    def test_status_enum(self):
        with pytest.raises(ValidationError):
            Hypothesis(
                id="HYP-001",
                title="t",
                status="bogus",  # type: ignore[arg-type]
                created=date(2026, 1, 1),
                source_task="HATS-001",
                hypothesis="h",
            )

    def test_extras_allowed_to_preserve_legacy_keys(self):
        h = Hypothesis.model_validate({
            "id": "HYP-008",
            "title": "t",
            "status": "active",
            "created": "2026-05-03",
            "source_task": "HATS-209",
            "hypothesis": "h",
            "custom_legacy_field": "preserved",
        })
        # extras="allow" keeps the unknown key on the model
        dumped = h.model_dump(exclude_none=True)
        assert dumped["custom_legacy_field"] == "preserved"

    def test_validation_log_entry_strict_verdict_enum(self):
        with pytest.raises(ValidationError):
            ValidationLogEntry(
                date=date(2026, 1, 1),
                verdict="maybe",  # type: ignore[arg-type]
                evidence="x",
            )

    def test_validation_log_entry_default_recommendation(self):
        e = ValidationLogEntry(
            date=date(2026, 1, 1), verdict="confirmed", evidence="x"
        )
        assert e.recommendation == "keep"

    def test_validation_log_entry_extras_allowed(self):
        e = ValidationLogEntry.model_validate({
            "date": "2026-05-03",
            "verdict": "refuted",
            "evidence": "x",
            "sweep_report": "/tmp/foo.md",  # legacy free-form key
        })
        dumped = e.model_dump(exclude_none=True)
        assert dumped["sweep_report"] == "/tmp/foo.md"

    def test_exit_criteria_strict(self):
        ec = ExitCriteria(confirm=["a"], refute=["b"], stalled=["c"])
        assert ec.confirm == ["a"]
        with pytest.raises(ValidationError):
            ExitCriteria.model_validate({"confirm": ["a"], "extra": "no"})


class TestProposal:
    def _vote(self, sid="s1", reasoning="ok"):
        return Vote(
            session_id=sid,
            timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc),
            reasoning=reasoning,
        )

    def test_minimal_proposal(self):
        p = Proposal(
            id="PROP-001",
            created=datetime(2026, 5, 4, tzinfo=timezone.utc),
            title="t",
            category="rule",
            target="dev_rule_x",
            description="d",
            rationale="r",
        )
        assert p.status == "open"
        assert p.votes == []

    def test_id_pattern_enforced(self):
        with pytest.raises(ValidationError):
            Proposal(
                id="bad",
                created=datetime(2026, 5, 4, tzinfo=timezone.utc),
                title="t",
                category="rule",
                target="x",
                description="d",
                rationale="r",
            )

    def test_extras_forbidden(self):
        with pytest.raises(ValidationError):
            Proposal.model_validate({
                "id": "PROP-001",
                "created": "2026-05-04T00:00:00+00:00",
                "title": "t",
                "category": "rule",
                "target": "x",
                "description": "d",
                "rationale": "r",
                "extra_field": "no",
            })

    def test_meta_proposal_with_failed_session_id(self):
        p = Proposal(
            id="PROP-002",
            created=datetime(2026, 5, 4, tzinfo=timezone.utc),
            title="reflect-session failed on session XXX",
            category="process",
            target="reflect-session",
            description="d",
            rationale="r",
            failed_session_id="20260504-120000-1",
        )
        assert p.failed_session_id == "20260504-120000-1"

    def test_vote_extras_forbidden(self):
        with pytest.raises(ValidationError):
            Vote.model_validate({
                "session_id": "s",
                "timestamp": "2026-05-04T00:00:00+00:00",
                "reasoning": "r",
                "extra": "x",
            })
