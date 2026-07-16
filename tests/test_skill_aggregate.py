"""Advisory aggregate skill-consistency reporter (HATS-871 / T11, slice 4).

Report-only: dangling refs, cross-package refs (resolve but couple two packages),
duplicate names, overlap candidates. The command ALWAYS exits 0.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats import skill_aggregate as agg
from ai_hats.paths import builtin_library_layers
from ai_hats.skill_sources import skill_source_roots


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
    trk = tmp_path / "ai_hats_tracker"
    _skill(lib, "backlog-create", body="prefer the sibling skill `backlog-manager`")
    _skill(trk, "backlog-manager")
    report = agg.aggregate_report([lib, trk])
    assert not report.dangling
    xp = report.cross_package
    assert len(xp) == 1
    assert xp[0].referrer_pkg == "library" and xp[0].target_pkg == "tracker"
    assert xp[0].target == "backlog-manager"


def test_bold_prose_ref_is_reported_cross_package(tmp_path):
    # Bold **name** (no `see skill` backticks) must still be caught as coupling.
    lib = tmp_path / "ai_hats_library"
    trk = tmp_path / "ai_hats_tracker"
    _skill(lib, "git-mastery", body="the agent decides during plan (see **backlog-manager**)")
    _skill(trk, "backlog-manager")
    report = agg.aggregate_report([lib, trk])
    assert not report.dangling  # bold mention of a known skill is not dangling
    assert any(r.target == "backlog-manager" and r.referrer_pkg == "library" for r in report.cross_package)


def test_bold_mention_of_unknown_token_is_not_dangling(tmp_path):
    # Prose bold that is NOT a known component must be ignored (no false dangling).
    pkg = tmp_path / "ai_hats_library"
    _skill(pkg, "solo", body="this is **important** and **bold** prose, not a ref")
    report = agg.aggregate_report([pkg])
    assert report.dangling == []
    assert report.cross_package == []


def test_duplicate_name_across_engine_packages_is_reported(tmp_path):
    lib = tmp_path / "ai_hats_library"
    trk = tmp_path / "ai_hats_tracker"
    _skill(lib, "backlog-manager")
    _skill(trk, "backlog-manager")
    report = agg.aggregate_report([lib, trk])
    names = [name for name, _ in report.duplicates]
    assert "skills:backlog-manager" in names


def test_project_override_is_not_a_duplicate(tmp_path):
    trk = tmp_path / "ai_hats_tracker"
    proj = tmp_path / "projlib"  # no package marker -> "project"
    _skill(trk, "backlog-manager")
    _skill(proj, "backlog-manager")
    report = agg.aggregate_report([trk, proj])
    assert report.duplicates == []  # legit override, not a cross-engine dup


def test_overlap_candidate_is_reported(tmp_path):
    pkg = tmp_path / "ai_hats_library"
    _skill(pkg, "one", desc="Use this to review a pull request for correctness bugs")
    _skill(pkg, "two", desc="Use this to review a pull request for correctness issues")
    report = agg.aggregate_report([pkg])
    assert any({a, b} == {"one", "two"} for a, b, _ in report.overlaps)


def test_cli_always_exits_zero(tmp_path, capsys):
    # Even with a dangling ref present, the advisory command exits 0.
    assert agg._main([]) == 0
    out = capsys.readouterr().out
    assert "aggregate skill-consistency report" in out


def test_live_backlog_create_to_manager_is_co_located():
    # ADR-0016: backlog-create AND backlog-manager both ship in the library
    # content layer, so the reference resolves WITHIN the library — neither
    # dangling (discoverable) nor cross-package (both in the "library" package).
    paths = list(builtin_library_layers()) + skill_source_roots()
    report = agg.aggregate_report(paths)

    dangling_bm = [r for r in report.dangling if r.target == "backlog-manager"]
    assert not dangling_bm, f"backlog-manager should be discoverable, got {dangling_bm}"

    xpkg_bm = [r for r in report.cross_package if r.target == "backlog-manager"]
    assert not xpkg_bm, f"backlog-manager must not couple across packages, got {xpkg_bm}"
