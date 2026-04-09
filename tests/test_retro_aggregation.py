"""Tests for aggregation schema and round-trip through loader/writer."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from ai_hats.retro.aggregation import (
    AggregationV1,
    FindingClusterSummary,
    FindingRef,
)
from ai_hats.retro.common import (
    Category,
    FixTarget,
    FixTargetKind,
    FixType,
    ProposedFix,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_aggregation(**overrides) -> dict:
    base = {
        "schema": "hats-aggregation/v1",
        "aggregation_id": "AGG-2026-04-09-001",
        "project": "ai-hats",
        "date": "2026-04-09",
        "strategy": "freq",
        "retros_analyzed": 3,
        "clusters": [],
    }
    base.update(overrides)
    return base


def _cluster_dict(**overrides) -> dict:
    base = {
        "cluster_id": "C1",
        "representative_title": "Tool explosion",
        "category": "process",
        "severity": "high",
        "root_cause_pattern": "excessive tool calls in single turn",
        "frequency": 3,
        "rate": 0.6,
        "source_findings": [
            {"judge_retro_file": "2026-04-08-judge-001.md", "finding_id": "F1"},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestAggregationV1:
    def test_happy_path(self) -> None:
        model = AggregationV1.model_validate(_minimal_aggregation())
        assert model.aggregation_id == "AGG-2026-04-09-001"
        assert model.clusters == []

    def test_with_clusters(self) -> None:
        data = _minimal_aggregation(clusters=[_cluster_dict()])
        model = AggregationV1.model_validate(data)
        assert len(model.clusters) == 1
        assert model.clusters[0].cluster_id == "C1"

    def test_rejects_invalid_aggregation_id(self) -> None:
        with pytest.raises(ValidationError, match="aggregation_id"):
            AggregationV1.model_validate(_minimal_aggregation(
                aggregation_id="BAD-FORMAT"
            ))

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            AggregationV1.model_validate(_minimal_aggregation(
                unknown_field="oops"
            ))

    def test_retros_analyzed_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            AggregationV1.model_validate(_minimal_aggregation(retros_analyzed=0))

    def test_since_is_optional(self) -> None:
        model = AggregationV1.model_validate(_minimal_aggregation(since="2026-04-01"))
        assert model.since == date(2026, 4, 1)


class TestFindingClusterSummary:
    def test_happy_path(self) -> None:
        model = FindingClusterSummary.model_validate(_cluster_dict())
        assert model.frequency == 3
        assert model.rate == pytest.approx(0.6)

    def test_rejects_invalid_cluster_id(self) -> None:
        with pytest.raises(ValidationError, match="cluster_id"):
            FindingClusterSummary.model_validate(_cluster_dict(cluster_id="X1"))

    def test_rejects_zero_frequency(self) -> None:
        with pytest.raises(ValidationError):
            FindingClusterSummary.model_validate(_cluster_dict(frequency=0))

    def test_with_target(self) -> None:
        data = _cluster_dict(target={"kind": "skill", "name": "git-mastery"})
        model = FindingClusterSummary.model_validate(data)
        assert model.target is not None
        assert model.target.kind == FixTargetKind.SKILL

    def test_with_proposed_fix(self) -> None:
        data = _cluster_dict(proposed_fix={
            "type": "skill_update",
            "target": {"kind": "skill", "name": "git-mastery"},
            "description": "Add batching rule",
        })
        model = FindingClusterSummary.model_validate(data)
        assert model.proposed_fix is not None
        assert model.proposed_fix.type == FixType.SKILL_UPDATE

    def test_rate_bounds(self) -> None:
        with pytest.raises(ValidationError):
            FindingClusterSummary.model_validate(_cluster_dict(rate=1.5))
        with pytest.raises(ValidationError):
            FindingClusterSummary.model_validate(_cluster_dict(rate=-0.1))


class TestFindingRef:
    def test_happy_path(self) -> None:
        ref = FindingRef.model_validate({
            "judge_retro_file": "2026-04-08-judge-001.md",
            "finding_id": "F1",
        })
        assert ref.finding_id == "F1"

    def test_rejects_bad_finding_id(self) -> None:
        with pytest.raises(ValidationError, match="finding_id"):
            FindingRef.model_validate({
                "judge_retro_file": "x.md",
                "finding_id": "BAD",
            })

    def test_rejects_empty_file(self) -> None:
        with pytest.raises(ValidationError):
            FindingRef.model_validate({
                "judge_retro_file": "",
                "finding_id": "F1",
            })


class TestRoundTrip:
    def test_loader_round_trip(self, tmp_path) -> None:
        from ai_hats.retro.loader import load
        from ai_hats.retro.writer import dump

        model = AggregationV1.model_validate(
            _minimal_aggregation(clusters=[_cluster_dict()])
        )
        path = tmp_path / "AGG-2026-04-09-001.md"
        dump(model, path, "# Test body\n")

        loaded, body = load(path)
        assert isinstance(loaded, AggregationV1)
        assert loaded.aggregation_id == model.aggregation_id
        assert len(loaded.clusters) == 1
        assert "Test body" in body
