"""Tests for the frequency-based finding clustering engine."""

from __future__ import annotations

import pytest

from ai_hats.retro.common import (
    Category,
    Evidence,
    EvidenceSource,
    Finding,
    FixTarget,
    FixTargetKind,
    FixType,
    ProposedFix,
    Severity,
)
from ai_hats.retro.frequency import (
    FindingWithSource,
    compute_frequencies,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    *,
    id: str = "F1",
    title: str = "test",
    category: Category = Category.PROCESS,
    severity: Severity = Severity.MEDIUM,
    root_cause: str = "some root cause",
    target_kind: FixTargetKind | None = None,
    target_name: str | None = None,
    fix_type: FixType | None = None,
) -> Finding:
    proposed_fix = None
    if fix_type is not None and target_kind is not None and target_name is not None:
        proposed_fix = ProposedFix(
            type=fix_type,
            target=FixTarget(kind=target_kind, name=target_name),
            description="fix it",
        )
    return Finding(
        id=id,
        title=title,
        category=category,
        severity=severity,
        root_cause=root_cause,
        evidence=[Evidence(
            session_id="20260408-120000-1",
            source=EvidenceSource.AUDIT,
            location="audit.md:Turn 1",
        )],
        proposed_fix=proposed_fix,
    )


def _fws(finding: Finding, source: str = "judge-001.md") -> FindingWithSource:
    return FindingWithSource(finding=finding, source_file=source)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeFrequenciesEmpty:
    def test_empty_input(self) -> None:
        assert compute_frequencies([]) == []


class TestComputeFrequenciesGrouping:
    def test_identical_category_and_root_cause_cluster_together(self) -> None:
        f1 = _fws(_finding(root_cause="tool explosion in turns"), "retro-1.md")
        f2 = _fws(_finding(root_cause="tool explosion in turns"), "retro-2.md")

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 1
        assert clusters[0].frequency == 2

    def test_different_categories_do_not_cluster(self) -> None:
        f1 = _fws(_finding(category=Category.PROCESS, root_cause="same cause"))
        f2 = _fws(_finding(category=Category.TOOLING, root_cause="same cause"))

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 2

    def test_different_targets_do_not_cluster(self) -> None:
        f1 = _fws(_finding(
            root_cause="same cause",
            target_kind=FixTargetKind.SKILL,
            target_name="skill-a",
            fix_type=FixType.SKILL_UPDATE,
        ))
        f2 = _fws(_finding(
            root_cause="same cause",
            target_kind=FixTargetKind.SKILL,
            target_name="skill-b",
            fix_type=FixType.SKILL_UPDATE,
        ))

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 2


class TestComputeFrequenciesFuzzy:
    def test_similar_root_causes_cluster_with_default_threshold(self) -> None:
        # These two are very similar (differ by one word).
        f1 = _fws(
            _finding(root_cause="agent performed excessive sequential directory listing"),
            "retro-1.md",
        )
        f2 = _fws(
            _finding(root_cause="agent performed excessive sequential directory exploration"),
            "retro-2.md",
        )

        clusters = compute_frequencies([f1, f2], fuzz_threshold=80)

        assert len(clusters) == 1
        assert clusters[0].frequency == 2

    def test_dissimilar_root_causes_stay_separate(self) -> None:
        f1 = _fws(_finding(root_cause="tool explosion"))
        f2 = _fws(_finding(root_cause="missing validation on user input"))

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 2

    def test_high_threshold_prevents_fuzzy_merge(self) -> None:
        f1 = _fws(_finding(root_cause="tool explosion in monolithic turns"))
        f2 = _fws(_finding(root_cause="tool explosion in large sessions"))

        clusters = compute_frequencies([f1, f2], fuzz_threshold=99)

        assert len(clusters) == 2


class TestComputeFrequenciesSeverity:
    def test_cluster_severity_is_max(self) -> None:
        f1 = _fws(_finding(severity=Severity.LOW, root_cause="same"))
        f2 = _fws(_finding(severity=Severity.HIGH, root_cause="same"))

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 1
        assert clusters[0].severity == Severity.HIGH

    def test_sorted_by_frequency_then_severity(self) -> None:
        # 2 findings in cluster A (medium), 1 finding in cluster B (critical)
        a1 = _fws(_finding(
            category=Category.PROCESS,
            severity=Severity.MEDIUM,
            root_cause="pattern A repeated",
        ), "retro-1.md")
        a2 = _fws(_finding(
            category=Category.PROCESS,
            severity=Severity.MEDIUM,
            root_cause="pattern A repeated",
        ), "retro-2.md")
        b1 = _fws(_finding(
            category=Category.TOOLING,
            severity=Severity.CRITICAL,
            root_cause="pattern B once",
        ), "retro-1.md")

        clusters = compute_frequencies([a1, a2, b1])

        assert len(clusters) == 2
        # First cluster has higher frequency
        assert clusters[0].frequency == 2
        assert clusters[0].category == Category.PROCESS


class TestComputeFrequenciesRate:
    def test_rate_counts_unique_retros(self) -> None:
        # Two findings from the same retro should count as one occurrence.
        f1 = _fws(_finding(id="F1", root_cause="same issue"), "retro-1.md")
        f2 = _fws(_finding(id="F2", root_cause="same issue"), "retro-1.md")

        clusters = compute_frequencies([f1, f2])

        assert len(clusters) == 1
        assert clusters[0].frequency == 2
        assert clusters[0].rate(total_retros=5) == pytest.approx(0.2)  # 1/5

    def test_rate_with_different_retros(self) -> None:
        f1 = _fws(_finding(root_cause="same"), "retro-1.md")
        f2 = _fws(_finding(root_cause="same"), "retro-2.md")
        f3 = _fws(_finding(root_cause="same"), "retro-3.md")

        clusters = compute_frequencies([f1, f2, f3])

        assert clusters[0].rate(total_retros=5) == pytest.approx(0.6)  # 3/5

    def test_rate_zero_retros(self) -> None:
        f1 = _fws(_finding(root_cause="x"))
        clusters = compute_frequencies([f1])
        assert clusters[0].rate(total_retros=0) == 0.0


class TestComputeFrequenciesProposedFix:
    def test_best_fix_from_highest_severity(self) -> None:
        f_low = _fws(_finding(
            severity=Severity.LOW,
            root_cause="same pattern",
            target_kind=FixTargetKind.SKILL,
            target_name="my-skill",
            fix_type=FixType.SKILL_UPDATE,
        ))
        f_high = _fws(_finding(
            severity=Severity.HIGH,
            root_cause="same pattern",
            target_kind=FixTargetKind.SKILL,
            target_name="my-skill",
            fix_type=FixType.SKILL_CREATE,
        ))

        clusters = compute_frequencies([f_low, f_high])

        assert len(clusters) == 1
        assert clusters[0].proposed_fix is not None
        assert clusters[0].proposed_fix.type == FixType.SKILL_CREATE

    def test_no_fix_when_none_have_one(self) -> None:
        f1 = _fws(_finding(root_cause="x"))
        f2 = _fws(_finding(root_cause="x"))

        clusters = compute_frequencies([f1, f2])

        assert clusters[0].proposed_fix is None


class TestComputeFrequenciesCanonicalRootCause:
    def test_canonical_is_longest(self) -> None:
        f1 = _fws(_finding(root_cause="short"))
        f2 = _fws(_finding(root_cause="a much longer root cause description"))

        clusters = compute_frequencies([f1, f2], fuzz_threshold=0)
        # With threshold=0, everything in the same category clusters together.

        assert len(clusters) == 1
        assert clusters[0].canonical_root_cause == "a much longer root cause description"
