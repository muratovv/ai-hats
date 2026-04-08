"""Tests for retro schemas — common types and three top-level models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_hats.retro import (
    Category,
    Evidence,
    EvidenceSource,
    ExpectedImpact,
    Finding,
    FindingStatus,
    FixTarget,
    FixTargetKind,
    FixType,
    ProposedFix,
    SessionArtifacts,
    SessionLinks,
    SessionMetrics,
    Severity,
)
from ai_hats.retro.bundle import SCHEMA_VERSION as BUNDLE_VERSION
from ai_hats.retro.bundle import BundleV1
from ai_hats.retro.judge_retro import SCHEMA_VERSION as JUDGE_RETRO_VERSION
from ai_hats.retro.judge_retro import JudgeRetroV1
from ai_hats.retro.session_retro import SCHEMA_VERSION as SESSION_RETRO_VERSION
from ai_hats.retro.session_retro import SessionRetroV1


# --- helpers ---


def _evidence() -> Evidence:
    return Evidence(
        session_id="session_test",
        source=EvidenceSource.AUDIT,
        location="audit.md:Turn 1",
    )


def _expected_impact() -> ExpectedImpact:
    return ExpectedImpact(reduces_category=Category.PROCESS)


def _proposed_skill_fix(with_impact: bool = True) -> ProposedFix:
    return ProposedFix(
        type=FixType.SKILL_UPDATE,
        target=FixTarget(kind=FixTargetKind.SKILL, name="judge-protocol"),
        description="Add a check",
        expected_impact=_expected_impact() if with_impact else None,
    )


# --- happy paths ---


def test_finding_happy_path() -> None:
    f = Finding(
        id="F1",
        title="Test finding",
        category=Category.PROCESS,
        severity=Severity.MEDIUM,
        root_cause="rc",
        evidence=[_evidence()],
        proposed_fix=_proposed_skill_fix(),
        status=FindingStatus.TRACKED,
        task_ref="HATS-100",
    )
    assert f.id == "F1"
    assert f.status == FindingStatus.TRACKED


def test_session_metrics_computed_properties() -> None:
    m = SessionMetrics(
        exit_code=0,
        turns=10,
        tool_calls=50,
        tokens_in=100,
        cache_read=900,
    )
    assert m.cache_hit_ratio == 0.9
    assert m.tool_calls_per_turn == 5.0


def test_session_metrics_zero_turns_no_division_error() -> None:
    m = SessionMetrics(exit_code=0, turns=0, tool_calls=0)
    assert m.tool_calls_per_turn is None
    assert m.cache_hit_ratio is None


def test_session_retro_round_trip_via_alias() -> None:
    raw = {
        "schema": SESSION_RETRO_VERSION,
        "session_id": "session_test",
        "project": "test",
        "role": "go-dev",
        "date": "2026-04-08",
        "metrics": {"exit_code": 0, "turns": 5, "tool_calls": 12},
        "summary": "Test",
        "links": {"audit": "a.md"},
    }
    sr = SessionRetroV1.model_validate(raw)
    dumped = sr.model_dump(by_alias=True, mode="json", exclude_none=True)
    sr2 = SessionRetroV1.model_validate(dumped)
    assert sr2.session_id == sr.session_id


def test_bundle_happy_path() -> None:
    b = BundleV1.model_validate({
        "schema": BUNDLE_VERSION,
        "bundle_id": "BUNDLE-2026-04-08-001",
        "project": "test",
        "created": "2026-04-08T09:00:00Z",
        "session_ids": ["s1", "s2"],
        "notes": "test bundle",
    })
    assert b.bundle_id == "BUNDLE-2026-04-08-001"
    assert len(b.session_ids) == 2
    assert b.notes == "test bundle"


def test_bundle_rejects_focus_field() -> None:
    """`focus` was removed in favor of judge-time --focus; should be rejected."""
    from pydantic import ValidationError as _VE
    with pytest.raises(_VE):
        BundleV1.model_validate({
            "schema": BUNDLE_VERSION,
            "bundle_id": "BUNDLE-2026-04-08-001",
            "project": "test",
            "created": "2026-04-08T09:00:00Z",
            "session_ids": ["s1"],
            "focus": "rejected",
        })


def test_judge_retro_happy_path() -> None:
    jr = JudgeRetroV1.model_validate({
        "schema": JUDGE_RETRO_VERSION,
        "judge_run_id": "judge-001",
        "project": "test",
        "date": "2026-04-08",
        "bundle_id": "BUNDLE-2026-04-08-001",
        "findings": [{
            "id": "F1",
            "title": "x",
            "category": "process",
            "severity": "low",
            "root_cause": "rc",
            "evidence": [{
                "session_id": "session_test",
                "source": "audit",
                "location": "l",
            }],
        }],
    })
    assert jr.judge_run_id == "judge-001"
    assert len(jr.findings) == 1


# --- failure modes (5 from acceptance criteria) ---


def test_finding_rejects_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="F1",
            title="x",
            category=Category.PROCESS,
            severity=Severity.LOW,
            root_cause="rc",
            evidence=[],
        )


def test_finding_rejects_tracked_without_task_ref() -> None:
    with pytest.raises(ValidationError, match="task_ref"):
        Finding(
            id="F1",
            title="x",
            category=Category.PROCESS,
            severity=Severity.LOW,
            root_cause="rc",
            evidence=[_evidence()],
            status=FindingStatus.TRACKED,
        )


def test_finding_rejects_tracked_skill_fix_without_expected_impact() -> None:
    with pytest.raises(ValidationError, match="expected_impact"):
        Finding(
            id="F1",
            title="x",
            category=Category.PROCESS,
            severity=Severity.LOW,
            root_cause="rc",
            evidence=[_evidence()],
            proposed_fix=_proposed_skill_fix(with_impact=False),
            status=FindingStatus.TRACKED,
            task_ref="X-1",
        )


def test_proposed_fix_rejects_skill_update_without_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        ProposedFix(type=FixType.SKILL_UPDATE, description="x")


def test_proposed_fix_no_action_does_not_require_target() -> None:
    fix = ProposedFix(type=FixType.NO_ACTION, description="judgment call")
    assert fix.target is None


def test_session_retro_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        SessionRetroV1.model_validate({
            "schema": SESSION_RETRO_VERSION,
            "session_id": "x",
            "project": "p",
            "role": "r",
            "date": "2026-01-01",
            "metrics": {"exit_code": 0, "turns": 1, "tool_calls": 1},
            "summary": "s",
            "links": {"audit": "a.md"},
            "spurious_field": "nope",
        })


def test_session_retro_rejects_wrong_schema_literal() -> None:
    with pytest.raises(ValidationError):
        SessionRetroV1.model_validate({
            "schema": "hats-session-retro/v999",
            "session_id": "x",
            "project": "p",
            "role": "r",
            "date": "2026-01-01",
            "metrics": {"exit_code": 0, "turns": 1, "tool_calls": 1},
            "summary": "s",
            "links": {"audit": "a.md"},
        })


def test_bundle_rejects_invalid_id_format() -> None:
    for bad_id in [
        "bundle-2026-04-08-001",  # lowercase
        "BUNDLE-26-4-8-1",         # short date
        "BUNDLE-2026-04-08",       # missing counter
        "BUNDLE-2026-04-08-1",     # short counter
    ]:
        with pytest.raises(ValidationError):
            BundleV1.model_validate({
                "schema": BUNDLE_VERSION,
                "bundle_id": bad_id,
                "project": "p",
                "created": "2026-04-08T00:00:00Z",
                "session_ids": ["s1"],
            })


def test_bundle_rejects_empty_session_ids() -> None:
    with pytest.raises(ValidationError):
        BundleV1.model_validate({
            "schema": BUNDLE_VERSION,
            "bundle_id": "BUNDLE-2026-04-08-001",
            "project": "p",
            "created": "2026-04-08T00:00:00Z",
            "session_ids": [],
        })


def test_judge_retro_rejects_empty_findings() -> None:
    with pytest.raises(ValidationError):
        JudgeRetroV1.model_validate({
            "schema": JUDGE_RETRO_VERSION,
            "judge_run_id": "x",
            "project": "p",
            "date": "2026-01-01",
            "bundle_id": "BUNDLE-2026-01-01-001",
            "findings": [],
        })


def test_finding_id_pattern_supports_letter_suffix() -> None:
    f = Finding(
        id="F5b",
        title="x",
        category=Category.PROCESS,
        severity=Severity.LOW,
        root_cause="rc",
        evidence=[_evidence()],
    )
    assert f.id == "F5b"


def test_finding_id_pattern_rejects_arbitrary() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="finding-1",
            title="x",
            category=Category.PROCESS,
            severity=Severity.LOW,
            root_cause="rc",
            evidence=[_evidence()],
        )


def test_session_links_default_artifacts_lists_are_independent() -> None:
    """Regression guard: default_factory must not share list across instances."""
    a = SessionArtifacts()
    b = SessionArtifacts()
    a.commits.append("sha1")
    assert b.commits == []
    # SessionLinks is unrelated; just verify minimal construction
    sl = SessionLinks(audit="a.md")
    assert sl.metrics is None
