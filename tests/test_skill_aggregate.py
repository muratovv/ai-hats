"""Advisory aggregate skill-consistency reporter (HATS-871 / T11, slice 4).

Report-only: dangling refs, cross-package refs (resolve but couple two packages),
duplicate names, overlap candidates. The command ALWAYS exits 0.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats import skill_aggregate as agg
from ai_hats.paths import builtin_library_layers


def _skill(root: Path, name: str, body: str = "", desc: str = "d") -> None:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n")


def test_dangling_ref_is_reported(tmp_path):
    pkg = tmp_path / "ai_hats_library"
    _skill(pkg, "a-skill", body="see skill `ghost` for details")
    report = agg.aggregate_report([pkg])
    assert [r.target for r in report.dangling] == ["ghost"]
    assert not report.cross_package


def test_cross_package_ref_is_reported_not_dangling(tmp_path):
    lib = tmp_path / "ai_hats_library"
    wtp = tmp_path / "ai_hats_wt"
    _skill(lib, "backlog-create", body="prefer the sibling skill `backlog-manager`")
    _skill(wtp, "backlog-manager")
    report = agg.aggregate_report([lib, wtp])
    assert not report.dangling
    xp = report.cross_package
    assert len(xp) == 1
    assert xp[0].referrer_pkg == "library" and xp[0].target_pkg == "wt"
    assert xp[0].target == "backlog-manager"


def test_bold_prose_ref_is_reported_cross_package(tmp_path):
    # Bold **name** (no `see skill` backticks) must still be caught as coupling.
    lib = tmp_path / "ai_hats_library"
    wtp = tmp_path / "ai_hats_wt"
    _skill(lib, "git-mastery", body="the agent decides during plan (see **backlog-manager**)")
    _skill(wtp, "backlog-manager")
    report = agg.aggregate_report([lib, wtp])
    assert not report.dangling  # bold mention of a known skill is not dangling
    assert any(
        r.target == "backlog-manager" and r.referrer_pkg == "library" for r in report.cross_package
    )


def test_bold_mention_of_unknown_token_is_not_dangling(tmp_path):
    # Prose bold that is NOT a known component must be ignored (no false dangling).
    pkg = tmp_path / "ai_hats_library"
    _skill(pkg, "solo", body="this is **important** and **bold** prose, not a ref")
    report = agg.aggregate_report([pkg])
    assert report.dangling == []
    assert report.cross_package == []


def test_duplicate_name_across_engine_packages_is_reported(tmp_path):
    lib = tmp_path / "ai_hats_library"
    wtp = tmp_path / "ai_hats_wt"
    _skill(lib, "backlog-manager")
    _skill(wtp, "backlog-manager")
    report = agg.aggregate_report([lib, wtp])
    names = [name for name, _ in report.duplicates]
    assert "skills:backlog-manager" in names


def test_project_override_is_not_a_duplicate(tmp_path):
    wtp = tmp_path / "ai_hats_wt"
    proj = tmp_path / "projlib"  # no package marker -> "project"
    _skill(wtp, "backlog-manager")
    _skill(proj, "backlog-manager")
    report = agg.aggregate_report([wtp, proj])
    assert report.duplicates == []  # legit override, not a cross-engine dup


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_aggregate_report_against_builtin_packages():
    roots = builtin_library_layers(REPO_ROOT)
    report = agg.aggregate_report(roots)
    assert isinstance(report, agg.AggregateReport)
