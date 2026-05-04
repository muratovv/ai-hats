"""Schema validation tests for ReflectSessionV1."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_hats.retro.reflect_session_schema import (
    HypothesisVerdict,
    ProposalAction,
    ReflectSessionV1,
)


def _full_payload(**kw):
    base = {
        "schema": "hats-reflect-session/v1",
        "session_id": "20260504-120000-1",
        "timestamp": datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc).isoformat(),
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    }
    base.update(kw)
    return base


def test_minimal_valid_payload():
    rs = ReflectSessionV1.model_validate(_full_payload())
    assert rs.session_id == "20260504-120000-1"
    assert rs.hypothesis_verdicts == []


def test_schema_literal_enforced():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(schema="wrong/v1"))


def test_extras_forbidden():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(extra_field="x"))


def test_hypothesis_verdict_id_pattern():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(
            hypothesis_verdicts=[{
                "hyp_id": "bad",
                "verdict": "confirmed",
                "evidence": "x",
            }]
        ))


def test_hypothesis_verdict_enum():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(
            hypothesis_verdicts=[{
                "hyp_id": "HYP-001",
                "verdict": "maybe",
                "evidence": "x",
            }]
        ))


def test_hypothesis_verdict_evidence_required():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(
            hypothesis_verdicts=[{
                "hyp_id": "HYP-001",
                "verdict": "confirmed",
                "evidence": "",
            }]
        ))


def test_proposal_action_pattern():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(
            proposal_actions=[{"action": "created", "prop_id": "bad"}]
        ))


def test_proposal_action_enum():
    with pytest.raises(ValidationError):
        ReflectSessionV1.model_validate(_full_payload(
            proposal_actions=[{"action": "deleted", "prop_id": "PROP-001"}]
        ))


def test_self_problems_list_of_strings():
    rs = ReflectSessionV1.model_validate(_full_payload(
        self_problems=["PROP-005", "PROP-006"]
    ))
    assert rs.self_problems == ["PROP-005", "PROP-006"]


def test_full_round_trip():
    payload = _full_payload(
        hypothesis_verdicts=[
            {
                "hyp_id": "HYP-008",
                "verdict": "inconclusive",
                "evidence": "session has no Bash anti-pattern usage to test",
                "recommendation": "extend_window",
            }
        ],
        proposal_actions=[
            {"action": "created", "prop_id": "PROP-007"},
            {"action": "voted", "prop_id": "PROP-003"},
        ],
        self_problems=["PROP-008"],
    )
    rs = ReflectSessionV1.model_validate(payload)
    assert rs.hypothesis_verdicts[0].recommendation == "extend_window"
    assert rs.proposal_actions[1].action == "voted"


def test_loader_dispatch_includes_reflect_session():
    from ai_hats.retro.loader import SCHEMA_FAMILY_TO_MODEL
    assert SCHEMA_FAMILY_TO_MODEL["hats-reflect-session"] is ReflectSessionV1
