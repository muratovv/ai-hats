"""Aggregator runtime: load judge retros, cluster findings, save report.

Reads all JudgeRetroV1 files from .agent/retrospectives/judge/, runs
the frequency engine, and writes an AggregationV1 report.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .aggregation import (
    AggregationV1,
    FindingClusterSummary,
    FindingRef,
)
from .common import Severity
from .frequency import FindingWithSource, compute_frequencies
from .judge_retro import JudgeRetroV1
from .loader import load

_JUDGE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-judge-\d{3}\.md$")
_AGG_FILE_RE = re.compile(r"^AGG-(\d{4}-\d{2}-\d{2})-(\d{3})\.md$")

_SEVERITY_INDEX = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class Aggregator:
    """Aggregate findings from judge retros into frequency clusters."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.judge_dir = project_dir / ".agent" / "retrospectives" / "judge"
        self.agg_dir = project_dir / ".agent" / "retrospectives" / "aggregated"

    def aggregate(
        self,
        *,
        strategy: str = "freq",
        since: date | None = None,
        min_severity: Severity | None = None,
    ) -> Path:
        """Load judge retros, aggregate, save report. Returns output path."""
        retros = self._load_judge_retros(since=since)
        if not retros:
            raise ValueError(
                "No judge retros found"
                + (f" since {since}" if since else "")
            )

        findings = self._extract_findings(retros, min_severity=min_severity)
        if not findings:
            raise ValueError("No findings match the given filters")

        if strategy != "freq":
            raise NotImplementedError(f"Strategy {strategy!r} not yet implemented")

        model = self._freq_strategy(
            findings=findings,
            retro_count=len(retros),
            since=since,
        )
        return self._save(model)

    def _load_judge_retros(
        self,
        *,
        since: date | None = None,
    ) -> list[tuple[str, JudgeRetroV1]]:
        """Load all judge retro files, optionally filtered by date."""
        if not self.judge_dir.is_dir():
            return []

        result: list[tuple[str, JudgeRetroV1]] = []
        for path in sorted(self.judge_dir.iterdir()):
            if not _JUDGE_FILE_RE.match(path.name):
                continue
            artifact, _body = load(path)
            if not isinstance(artifact, JudgeRetroV1):
                continue
            if since and artifact.date < since:
                continue
            result.append((path.name, artifact))

        return result

    def _extract_findings(
        self,
        retros: list[tuple[str, JudgeRetroV1]],
        *,
        min_severity: Severity | None = None,
    ) -> list[FindingWithSource]:
        """Flatten all findings from all retros into a single list."""
        min_idx = _SEVERITY_INDEX[min_severity] if min_severity else 0
        result: list[FindingWithSource] = []
        for filename, retro in retros:
            for finding in retro.findings:
                if _SEVERITY_INDEX[finding.severity] >= min_idx:
                    result.append(FindingWithSource(
                        finding=finding,
                        source_file=filename,
                    ))
        return result

    def _freq_strategy(
        self,
        *,
        findings: list[FindingWithSource],
        retro_count: int,
        since: date | None,
    ) -> AggregationV1:
        """Deterministic frequency-based aggregation."""
        clusters = compute_frequencies(findings)

        cluster_summaries: list[FindingClusterSummary] = []
        for i, cluster in enumerate(clusters, 1):
            source_refs = [
                FindingRef(
                    judge_retro_file=f.source_file,
                    finding_id=f.finding.id,
                )
                for f in cluster.findings
            ]
            cluster_summaries.append(FindingClusterSummary(
                cluster_id=f"C{i}",
                representative_title=cluster.findings[0].finding.title,
                category=cluster.category,
                severity=cluster.severity,
                target=cluster.target,
                root_cause_pattern=cluster.canonical_root_cause,
                frequency=cluster.frequency,
                rate=cluster.rate(retro_count),
                source_findings=source_refs,
                proposed_fix=cluster.proposed_fix,
            ))

        project = self.project_dir.name
        today = date.today()

        return AggregationV1(
            aggregation_id=self._next_agg_id(today),
            project=project,
            date=today,
            strategy="freq",
            retros_analyzed=retro_count,
            since=since,
            clusters=cluster_summaries,
        )

    def _next_agg_id(self, today: date) -> str:
        """Generate AGG-YYYY-MM-DD-NNN with daily counter."""
        prefix = today.strftime("AGG-%Y-%m-%d-")
        max_seq = 0
        if self.agg_dir.is_dir():
            for path in self.agg_dir.iterdir():
                m = _AGG_FILE_RE.match(path.name)
                if m and m.group(1) == today.isoformat():
                    max_seq = max(max_seq, int(m.group(2)))
        return f"{prefix}{max_seq + 1:03d}"

    def _save(self, model: AggregationV1) -> Path:
        """Save aggregation report as frontmatter + markdown body."""
        from .writer import dump

        self.agg_dir.mkdir(parents=True, exist_ok=True)
        path = self.agg_dir / f"{model.aggregation_id}.md"
        body = self._render_body(model)
        dump(model, path, body)
        return path

    @staticmethod
    def _render_body(model: AggregationV1) -> str:
        """Render human-readable markdown body for the aggregation report."""
        lines: list[str] = []
        lines.append(f"# Aggregation: {model.aggregation_id}\n")
        lines.append(f"**Strategy:** {model.strategy}  ")
        lines.append(f"**Retros analyzed:** {model.retros_analyzed}  ")
        if model.since:
            lines.append(f"**Since:** {model.since}  ")
        lines.append(f"**Clusters found:** {len(model.clusters)}\n")

        if not model.clusters:
            lines.append("No recurring patterns found.\n")
            return "\n".join(lines)

        for cluster in model.clusters:
            sev = cluster.severity.value.upper()
            lines.append(f"## {cluster.cluster_id}: {cluster.representative_title}\n")
            lines.append(f"- **Category:** {cluster.category.value}")
            lines.append(f"- **Severity:** {sev}")
            lines.append(f"- **Frequency:** {cluster.frequency} findings "
                         f"({cluster.rate:.0%} of retros)")
            if cluster.target:
                lines.append(f"- **Target:** {cluster.target.kind.value}::{cluster.target.name}")
            lines.append(f"- **Root cause:** {cluster.root_cause_pattern}")
            if cluster.proposed_fix:
                lines.append(f"- **Proposed fix:** [{cluster.proposed_fix.type.value}] "
                             f"{cluster.proposed_fix.description}")
            lines.append("- **Sources:** "
                         + ", ".join(f"{r.judge_retro_file}:{r.finding_id}"
                                     for r in cluster.source_findings))
            lines.append("")

        return "\n".join(lines)
