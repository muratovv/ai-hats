"""Frequency-based clustering of judge findings.

Pure computation module — no I/O. Used by the aggregator to surface
recurring patterns across multiple judge retros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from .common import Category, Finding, FixTarget, FixTargetKind, ProposedFix, Severity

# ---------------------------------------------------------------------------
# rapidfuzz with difflib fallback
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _rf_fuzz

    def _fuzz_ratio(a: str, b: str) -> float:
        """Return similarity ratio 0–100 via rapidfuzz."""
        return _rf_fuzz.ratio(a, b)

except ImportError:
    from difflib import SequenceMatcher

    def _fuzz_ratio(a: str, b: str) -> float:  # type: ignore[misc]
        """Fallback: difflib SequenceMatcher ratio scaled to 0–100."""
        return SequenceMatcher(None, a, b).ratio() * 100


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingWithSource:
    """A finding paired with the filename of its source judge retro."""

    finding: Finding
    source_file: str


@dataclass(frozen=True)
class _ClusterKey:
    """Grouping key for the first pass (exact match)."""

    category: Category
    target_kind: FixTargetKind | None
    target_name: str | None


@dataclass
class FindingCluster:
    """A cluster of related findings across judge retros."""

    category: Category
    severity: Severity
    target: FixTarget | None
    canonical_root_cause: str
    findings: list[FindingWithSource] = field(default_factory=list)
    proposed_fix: ProposedFix | None = None

    @property
    def frequency(self) -> int:
        """Number of findings in this cluster."""
        return len(self.findings)

    def rate(self, total_retros: int) -> float:
        """Fraction of judge retros that contain this pattern."""
        if total_retros <= 0:
            return 0.0
        # Count unique source files (a retro may contribute multiple findings
        # to the same cluster, but that counts as one occurrence).
        unique_retros = len({f.source_file for f in self.findings})
        return unique_retros / total_retros


# ---------------------------------------------------------------------------
# Severity ordering for max()
# ---------------------------------------------------------------------------

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def _max_severity(a: Severity, b: Severity) -> Severity:
    return a if _SEVERITY_ORDER[a] >= _SEVERITY_ORDER[b] else b


def _best_proposed_fix(findings: list[FindingWithSource]) -> ProposedFix | None:
    """Pick the proposed_fix from the highest-severity finding that has one."""
    candidates = [
        f for f in findings if f.finding.proposed_fix is not None
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda f: _SEVERITY_ORDER[f.finding.severity], reverse=True
    )
    return candidates[0].finding.proposed_fix


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _cluster_key(f: FindingWithSource) -> _ClusterKey:
    target = f.finding.proposed_fix.target if f.finding.proposed_fix else None
    return _ClusterKey(
        category=f.finding.category,
        target_kind=target.kind if target else None,
        target_name=target.name if target else None,
    )


def _fuzzy_cluster(
    items: list[FindingWithSource],
    threshold: int,
) -> list[list[FindingWithSource]]:
    """Sub-cluster findings by fuzzy-matching root_cause strings.

    Simple single-linkage: each new finding joins the first cluster
    whose canonical (longest) root_cause matches above threshold.
    """
    clusters: list[list[FindingWithSource]] = []
    canonicals: list[str] = []

    for item in items:
        rc = item.finding.root_cause.lower().strip()
        matched = False
        for i, canonical in enumerate(canonicals):
            if _fuzz_ratio(rc, canonical) >= threshold:
                clusters[i].append(item)
                # Update canonical to the longest root_cause for stability.
                if len(rc) > len(canonical):
                    canonicals[i] = rc
                matched = True
                break
        if not matched:
            clusters.append([item])
            canonicals.append(rc)

    return clusters


def compute_frequencies(
    findings: list[FindingWithSource],
    *,
    fuzz_threshold: int = 80,
) -> list[FindingCluster]:
    """Group findings by (category, target), fuzzy-cluster root_cause.

    Returns clusters sorted by frequency descending, then severity descending.
    """
    if not findings:
        return []

    # Pass 1: exact grouping by (category, target_kind, target_name)
    sorted_findings = sorted(findings, key=lambda f: (
        f.finding.category.value,
        (_cluster_key(f).target_kind or "").value if _cluster_key(f).target_kind else "",
        _cluster_key(f).target_name or "",
    ))

    result: list[FindingCluster] = []

    for key, group_iter in groupby(sorted_findings, key=_cluster_key):
        group = list(group_iter)

        # Pass 2: fuzzy sub-clustering within each group
        sub_clusters = _fuzzy_cluster(group, fuzz_threshold)

        for sub in sub_clusters:
            # Compute aggregate fields
            sev = sub[0].finding.severity
            for item in sub[1:]:
                sev = _max_severity(sev, item.finding.severity)

            # Canonical root_cause = the longest one (after lowering)
            canonical = max(
                (item.finding.root_cause for item in sub), key=len
            )

            # Reconstruct FixTarget from key if present
            target = (
                FixTarget(kind=key.target_kind, name=key.target_name)
                if key.target_kind is not None and key.target_name is not None
                else None
            )

            result.append(FindingCluster(
                category=key.category,
                severity=sev,
                target=target,
                canonical_root_cause=canonical,
                findings=sub,
                proposed_fix=_best_proposed_fix(sub),
            ))

    # Sort: frequency desc, then severity desc
    result.sort(
        key=lambda c: (c.frequency, _SEVERITY_ORDER[c.severity]),
        reverse=True,
    )

    return result
