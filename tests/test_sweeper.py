"""Generic unclaimed-marker sweeper (HATS-905 phase 1).

Content-proof matrix: an entry is discarded only when its owner is dead AND
engine ownership of the CONTENT is proven (hash / embedded marker / legacy).
"""

from __future__ import annotations

import hashlib

import pytest

from ai_hats import owners, sweeper
from ai_hats.paths import claude_dir, claude_settings_json, claude_skills_dir


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = owners.registered()
    owners._reset_for_tests()
    yield
    owners._reset_for_tests()
    for key, module in snapshot.items():
        owners.register_owner(key, module=module)


SURFACE = sweeper.LineManifestSurface(
    owner_key="test-mech",
    marker_relpath=".testsurface/.ai-hats-manifest",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _seed(project_dir, *lines, header="# ai-hats-owner: test-mech"):
    base = project_dir / ".testsurface"
    base.mkdir(parents=True, exist_ok=True)
    marker = base / ".ai-hats-manifest"
    marker.write_text("\n".join([header, *lines]) + "\n")
    return base, marker


def test_living_owner_marker_untouched(tmp_path):
    owners.register_owner("test-mech", module="tests")
    base, marker = _seed(tmp_path, f"{_digest(b'x')}  victim.txt")
    (base / "victim.txt").write_bytes(b"x")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports == []
    assert marker.is_file()
    assert (base / "victim.txt").exists()


def test_user_edited_entry_kept_and_marker_rewritten(tmp_path):
    base, marker = _seed(
        tmp_path,
        f"{_digest(b'pristine')}  pristine.txt",
        f"{_digest(b'original')}  edited.txt",
    )
    (base / "pristine.txt").write_bytes(b"pristine")
    (base / "edited.txt").write_bytes(b"user changed this")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].swept == ("pristine.txt",)
    assert reports[0].kept == ("edited.txt",)
    assert not reports[0].marker_removed
    assert (base / "edited.txt").read_bytes() == b"user changed this"
    remaining = marker.read_text()
    assert "edited.txt" in remaining
    assert "pristine.txt" not in remaining
    assert remaining.startswith("# ai-hats-owner: test-mech")


def test_missing_victim_resolves_silently(tmp_path):
    base, marker = _seed(tmp_path, f"{_digest(b'x')}  gone.txt")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].swept == ()
    assert reports[0].kept == ()
    assert reports[0].marker_removed
    assert not marker.exists()


def test_hashless_entry_with_embedded_marker_swept(tmp_path):
    surface = sweeper.LineManifestSurface(
        owner_key="test-mech",
        marker_relpath=".testsurface/.ai-hats-manifest",
        embedded_marker="OWNED-BY-TEST-MECH",
    )
    base, marker = _seed(tmp_path, "dispatcher.sh", header="# a comment")
    (base / "dispatcher.sh").write_text("#!/bin/sh\n# OWNED-BY-TEST-MECH\n")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,))

    assert reports[0].swept == ("dispatcher.sh",)
    assert not (base / "dispatcher.sh").exists()


def test_hashless_entry_without_embedded_marker_kept(tmp_path):
    surface = sweeper.LineManifestSurface(
        owner_key="test-mech",
        marker_relpath=".testsurface/.ai-hats-manifest",
        embedded_marker="OWNED-BY-TEST-MECH",
    )
    base, marker = _seed(tmp_path, "taken.sh", header="# a comment")
    (base / "taken.sh").write_text("#!/bin/sh\n# user rewrote me\n")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,))

    assert reports[0].swept == ()
    assert reports[0].kept == ("taken.sh",)
    assert (base / "taken.sh").exists()


def test_legacy_surface_sweeps_hashless_entries(tmp_path):
    surface = sweeper.LineManifestSurface(
        owner_key="skills-export-like",
        marker_relpath=".testsurface/.ai-hats-manifest",
        legacy=True,
    )
    base, marker = _seed(tmp_path, "mirror-dir", header="# legacy, no header")
    (base / "mirror-dir").mkdir()
    (base / "mirror-dir" / "SKILL.md").write_text("stale copy")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,))

    assert reports[0].owner_key == "skills-export-like"
    assert reports[0].swept == ("mirror-dir",)
    assert not (base / "mirror-dir").exists()
    assert not marker.exists()


def test_nested_relative_entry_swept(tmp_path):
    payload = b"#!/bin/sh\nexit 0\n"
    base, marker = _seed(tmp_path, f"{_digest(payload)}  pre_commit.d/x.sh")
    (base / "pre_commit.d").mkdir()
    (base / "pre_commit.d" / "x.sh").write_bytes(payload)

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].swept == ("pre_commit.d/x.sh",)
    assert not (base / "pre_commit.d" / "x.sh").exists()


def test_dead_owner_hash_matching_entry_swept_with_marker(tmp_path):
    base, marker = _seed(tmp_path, f"{_digest(b'x')}  victim.txt")
    (base / "victim.txt").write_bytes(b"x")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert len(reports) == 1
    report = reports[0]
    assert report.owner_key == "test-mech"
    assert report.swept == ("victim.txt",)
    assert report.kept == ()
    assert report.marker_removed
    assert not (base / "victim.txt").exists()
    assert not marker.exists()


@pytest.mark.parametrize(
    "evil",
    ["../escape.txt", "/etc/hosts", "a/../../up.txt"],
)
def test_poisoned_marker_refused_whole_surface_zero_deletions(tmp_path, evil):
    base, marker = _seed(
        tmp_path,
        f"{_digest(b'x')}  good.txt",
        f"{_digest(b'y')}  {evil}",
    )
    (base / "good.txt").write_bytes(b"x")
    outside = tmp_path / "escape.txt"
    outside.write_bytes(b"y")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].refused is not None
    assert reports[0].swept == ()
    assert (base / "good.txt").exists()
    assert outside.exists()
    assert marker.is_file()


def test_symlink_dir_entry_unlinks_link_only(tmp_path):
    target = tmp_path / "outside-dir"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"precious")
    base, marker = _seed(tmp_path, "linked", header="# no header")
    (base / "linked").symlink_to(target)
    surface = sweeper.LineManifestSurface(
        owner_key="test-mech",
        marker_relpath=".testsurface/.ai-hats-manifest",
        legacy=True,
    )

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,))

    assert reports[0].swept == ("linked",)
    assert not (base / "linked").is_symlink()
    assert (target / "keep.txt").read_bytes() == b"precious"


def test_malformed_hashed_line_refuses_marker(tmp_path):
    base, marker = _seed(tmp_path, "not-a-hashed-line")
    (base / "not-a-hashed-line").write_bytes(b"z")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].refused is not None
    assert (base / "not-a-hashed-line").exists()
    assert marker.is_file()


def test_empty_owner_header_refuses_marker(tmp_path):
    _seed(tmp_path, "whatever", header="# ai-hats-owner:")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,))

    assert reports[0].refused is not None


def test_no_marker_no_report(tmp_path):
    assert sweeper.sweep_unclaimed(tmp_path, surfaces=(SURFACE,)) == []


SETTINGS_SURFACE = sweeper.SettingsTagsSurface(owner_key="tags-mech")


def _seed_settings(project_dir):
    import json

    settings = claude_settings_json(project_dir)
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "model": "user-choice",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "_ai_hats_managed": "ai-hats:hats-437"},
                        {"matcher": "Bash", "hooks": [{"command": "my-own.sh"}]},
                    ],
                    "SessionStart": [
                        {"_ai_hats_managed": "ai-hats:skill:SessionStart:*"},
                    ],
                },
            }
        )
    )
    return settings


def test_dead_owner_tagged_settings_entries_removed_user_kept(tmp_path):
    import json

    settings = _seed_settings(tmp_path)

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SETTINGS_SURFACE,))

    assert len(reports) == 1
    assert sorted(reports[0].swept) == [
        "ai-hats:hats-437",
        "ai-hats:skill:SessionStart:*",
    ]
    data = json.loads(settings.read_text())
    assert data["model"] == "user-choice"
    assert "SessionStart" not in data["hooks"]
    assert data["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [{"command": "my-own.sh"}]}]


def test_living_owner_settings_untouched(tmp_path):
    owners.register_owner("tags-mech", module="tests")
    settings = _seed_settings(tmp_path)
    before = settings.read_text()

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SETTINGS_SURFACE,))

    assert reports == []
    assert settings.read_text() == before


def test_broken_settings_json_refused(tmp_path):
    settings = claude_settings_json(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(SETTINGS_SURFACE,))

    assert reports[0].refused is not None
    assert settings.read_text() == "{not json"


def test_proc_surface_dead_owner_runs_shared_procedure(tmp_path):
    calls: list = []

    def fake_proc(project_dir):
        calls.append(project_dir)
        (tmp_path / ".probe").unlink()
        return ["a", "b"]

    (tmp_path / ".probe").write_text("x\n")
    surface = sweeper.ProcSurface(owner_key="legacy-mech", marker_relpath=".probe", proc=fake_proc)

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,))

    assert calls == [tmp_path]
    assert reports[0].swept == ("a", "b")
    assert reports[0].marker_removed


def test_proc_surface_living_owner_not_called(tmp_path):
    owners.register_owner("legacy-mech", module="tests")
    (tmp_path / ".probe").write_text("x\n")
    surface = sweeper.ProcSurface(
        owner_key="legacy-mech",
        marker_relpath=".probe",
        proc=lambda p: pytest.fail("must not run"),
    )

    assert sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,)) == []


def test_proc_surface_dry_run_reports_without_acting(tmp_path):
    (tmp_path / ".probe").write_text("x\n")
    surface = sweeper.ProcSurface(
        owner_key="legacy-mech",
        marker_relpath=".probe",
        proc=lambda p: pytest.fail("dry_run must not act"),
    )

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=(surface,), dry_run=True)

    assert reports[0].owner_key == "legacy-mech"
    assert (tmp_path / ".probe").exists()


def test_default_surfaces_cover_all_known_owners():
    keys = [s.owner_key for s in sweeper.default_surfaces()]

    assert keys == ["git-hooks", "runtime-hooks", "skills-export", "claude-publish"]


def test_default_surfaces_sweep_real_legacy_leftovers(tmp_path):
    # skills-export mirror (HATS-901 shape)
    skills = claude_skills_dir(tmp_path)
    skills.mkdir(parents=True)
    (skills / ".ai-hats-managed").write_text("old-skill\n")
    (skills / "old-skill").mkdir()
    (skills / "old-skill" / "SKILL.md").write_text("stale")
    # claude-publish manifest (pre-HATS-289 shape)
    claude = claude_dir(tmp_path)
    (claude / ".ai-hats-managed").write_text("role.md\nskills/keep\n")
    (claude / "role.md").write_text("legacy publish artefact")

    reports = sweeper.sweep_unclaimed(tmp_path, surfaces=sweeper.default_surfaces())

    by_owner = {r.owner_key: r for r in reports}
    assert "old-skill" in by_owner["skills-export"].swept
    assert "role.md" in by_owner["claude-publish"].swept
    assert not (claude / "role.md").exists()
    assert not (claude / ".ai-hats-managed").exists()
    assert not skills.exists()


def test_settings_without_tags_no_report(tmp_path):
    import json

    settings = claude_settings_json(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*"}]}}))

    assert sweeper.sweep_unclaimed(tmp_path, surfaces=(SETTINGS_SURFACE,)) == []
