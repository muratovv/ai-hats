"""Integration tests for the Aggregator runtime."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ai_hats.retro.aggregation import AggregationV1
from ai_hats.retro.aggregator import Aggregator
from ai_hats.retro.common import Severity
from ai_hats.retro.loader import load


# ---------------------------------------------------------------------------
# Helpers — write synthetic judge retro files
# ---------------------------------------------------------------------------


def _write_judge_retro(
    judge_dir: Path,
    filename: str,
    findings: list[dict],
    bundle_id: str = "BUNDLE-2026-04-08-001",
    retro_date: str = "2026-04-08",
) -> Path:
    """Write a minimal judge retro file with given findings."""
    import yaml

    fm = {
        "schema": "hats-judge-retro/v1",
        "judge_run_id": filename.replace(".md", ""),
        "project": "ai-hats",
        "date": retro_date,
        "bundle_id": bundle_id,
        "findings": findings,
    }
    yaml_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    path = judge_dir / filename
    path.write_text(f"---\n{yaml_text}---\n\n# Judge retro\n")
    return path


def _finding_dict(
    *,
    id: str = "F1",
    title: str = "Test finding",
    category: str = "process",
    severity: str = "medium",
    root_cause: str = "some root cause",
    target_kind: str | None = None,
    target_name: str | None = None,
    fix_type: str | None = None,
) -> dict:
    """Build a minimal finding dict for YAML serialization."""
    finding: dict = {
        "id": id,
        "title": title,
        "category": category,
        "severity": severity,
        "root_cause": root_cause,
        "evidence": [{
            "session_id": "20260408-120000-1",
            "source": "audit",
            "location": "audit.md:Turn 1",
        }],
    }
    if fix_type and target_kind and target_name:
        finding["proposed_fix"] = {
            "type": fix_type,
            "target": {"kind": target_kind, "name": target_name},
            "description": "fix it",
        }
    return finding


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Set up a project directory with judge retro dir."""
    judge_dir = tmp_path / ".agent" / "retrospectives" / "judge"
    judge_dir.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAggregatorLoadRetros:
    def test_loads_judge_retros(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(id="F1", root_cause="tool explosion"),
        ])
        _write_judge_retro(judge_dir, "2026-04-09-judge-001.md", [
            _finding_dict(id="F1", root_cause="tool explosion again"),
        ], retro_date="2026-04-09")

        agg = Aggregator(project)
        retros = agg._load_judge_retros()

        assert len(retros) == 2

    def test_since_filter(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-01-judge-001.md", [
            _finding_dict(),
        ], retro_date="2026-04-01")
        _write_judge_retro(judge_dir, "2026-04-09-judge-001.md", [
            _finding_dict(),
        ], retro_date="2026-04-09")

        agg = Aggregator(project)
        retros = agg._load_judge_retros(since=date(2026, 4, 5))

        assert len(retros) == 1
        assert retros[0][0] == "2026-04-09-judge-001.md"

    def test_empty_dir(self, project: Path) -> None:
        agg = Aggregator(project)
        assert agg._load_judge_retros() == []


class TestAggregatorAggregate:
    def test_basic_aggregation(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"

        # Two retros with similar findings → should cluster
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(id="F1", title="Tool explosion", root_cause="excessive tool calls in single turn"),
        ])
        _write_judge_retro(judge_dir, "2026-04-09-judge-001.md", [
            _finding_dict(id="F1", title="Tool explosion", root_cause="excessive tool calls in single turn"),
        ], retro_date="2026-04-09")

        agg = Aggregator(project)
        path = agg.aggregate()

        assert path.exists()
        assert path.name.startswith("AGG-")
        assert path.name.endswith(".md")

        # Load and validate
        loaded, body = load(path)
        assert isinstance(loaded, AggregationV1)
        assert loaded.retros_analyzed == 2
        assert len(loaded.clusters) == 1
        assert loaded.clusters[0].frequency == 2

    def test_min_severity_filter(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(id="F1", severity="low", root_cause="minor issue"),
            _finding_dict(id="F2", severity="high", root_cause="major issue"),
        ])

        agg = Aggregator(project)
        path = agg.aggregate(min_severity=Severity.HIGH)

        loaded, _ = load(path)
        assert isinstance(loaded, AggregationV1)
        # Only the high-severity finding should be included
        total_findings = sum(c.frequency for c in loaded.clusters)
        assert total_findings == 1

    def test_since_filter(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-01-judge-001.md", [
            _finding_dict(id="F1", root_cause="old"),
        ], retro_date="2026-04-01")
        _write_judge_retro(judge_dir, "2026-04-09-judge-001.md", [
            _finding_dict(id="F1", root_cause="new"),
        ], retro_date="2026-04-09")

        agg = Aggregator(project)
        path = agg.aggregate(since=date(2026, 4, 5))

        loaded, _ = load(path)
        assert isinstance(loaded, AggregationV1)
        assert loaded.retros_analyzed == 1
        assert loaded.since == date(2026, 4, 5)

    def test_no_retros_raises(self, project: Path) -> None:
        agg = Aggregator(project)
        with pytest.raises(ValueError, match="No judge retros found"):
            agg.aggregate()

    def test_no_matching_findings_raises(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(id="F1", severity="low"),
        ])

        agg = Aggregator(project)
        with pytest.raises(ValueError, match="No findings match"):
            agg.aggregate(min_severity=Severity.CRITICAL)

    def test_unsupported_strategy_raises(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(),
        ])

        agg = Aggregator(project)
        with pytest.raises(NotImplementedError, match="llm"):
            agg.aggregate(strategy="llm")


class TestAggregatorIdGeneration:
    def test_daily_counter_increments(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(id="F1", root_cause="issue A"),
        ])
        _write_judge_retro(judge_dir, "2026-04-09-judge-001.md", [
            _finding_dict(id="F1", root_cause="issue B"),
        ], retro_date="2026-04-09")

        agg = Aggregator(project)

        path1 = agg.aggregate()
        path2 = agg.aggregate()

        # Second run on same day should get incremented counter
        assert path1.name != path2.name


class TestAggregatorBody:
    def test_body_contains_cluster_info(self, project: Path) -> None:
        judge_dir = project / ".agent" / "retrospectives" / "judge"
        _write_judge_retro(judge_dir, "2026-04-08-judge-001.md", [
            _finding_dict(
                id="F1",
                title="Tool explosion",
                root_cause="excessive tool calls",
                severity="high",
            ),
        ])

        agg = Aggregator(project)
        path = agg.aggregate()
        _, body = load(path)

        assert "Tool explosion" in body
        assert "excessive tool calls" in body
        assert "HIGH" in body
