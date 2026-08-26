"""Tests for dry-run --materialize functionality (HATS-1551)."""

from __future__ import annotations

from pathlib import Path
import pytest

from ai_hats.dry_run import (
    DRY_RUN_MATERIALIZE_SESSION_ID,
    DRY_RUN_SESSION_ID,
    dry_run_automate,
    dry_run_hitl,
)
from ai_hats.paths import session_cache_dir


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    lib = tmp_path / "lib"
    skill = lib / "skills" / "s"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")
    role = lib / "roles" / "test-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\n"
        "composition:\n  skills: [s]\ninjection: Role body.\n"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    return proj


def test_dry_run_materialize_leaves_tree_on_disk(project: Path):
    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)
    assert not cache_mat.exists()

    report = dry_run_hitl(project, provider="claude", materialize=True)

    assert report.escapes == ()
    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert report.plan.entries

    # S3 / R6 & R7: Report includes notes stating tree location and synthetic sid
    assert any("materialized session tree written to disk at" in n for n in report.notes)
    assert any(
        f"synthetic session_id '{DRY_RUN_MATERIALIZE_SESSION_ID}'" in n for n in report.notes
    )


def test_dry_run_automate_materialize_leaves_tree_on_disk(project: Path):
    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)
    assert not cache_mat.exists()

    report = dry_run_automate(project, provider="claude", task="test task", materialize=True)

    assert report.escapes == ()
    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert report.plan.entries
    assert any("materialized session tree written to disk at" in n for n in report.notes)


def test_dry_run_default_leaves_nothing_on_disk(project: Path):
    cache_std = session_cache_dir(project, DRY_RUN_SESSION_ID)
    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)

    report = dry_run_hitl(project, provider="claude", materialize=False)

    assert report.escapes == ()
    assert not cache_std.exists()
    assert not cache_mat.exists()


def test_dry_run_materialize_determinism_on_repeated_runs(project: Path):
    """S5 / R4: Two --materialize runs in a row yield identical plan entries and files."""
    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)

    report1 = dry_run_hitl(project, provider="claude", materialize=True)
    entries1 = [(e.kind.value, str(e.target), e.size, e.digest) for e in report1.plan.entries]
    files1 = {str(p): p.read_bytes() for p in cache_mat.rglob("*") if p.is_file()}

    report2 = dry_run_hitl(project, provider="claude", materialize=True)
    entries2 = [(e.kind.value, str(e.target), e.size, e.digest) for e in report2.plan.entries]
    files2 = {str(p): p.read_bytes() for p in cache_mat.rglob("*") if p.is_file()}

    assert entries1 == entries2
    assert files1 == files2


def test_a_second_materialize_waits_instead_of_wiping_the_first(project: Path, monkeypatch):
    """A fixed sid means one directory for every run, so the rebuild is locked.

    HATS-1248 dropped the skills-mirror lock because a sid-keyed directory has
    exactly one writer. ``--materialize`` pins the sid, which brings the second
    writer back — and its first act is ``rmtree`` on the tree we are building.
    Asserted by holding the lock and watching the build refuse to proceed.
    Driven at the unit that owns the serialising, with the wait injected: the
    entry point resolves the real default, so reaching in to shorten it would
    patch the code under test (scripts/check_test_isolation.py).
    """  # comment-length: allow — the argument this re-opens is worth naming
    import filelock

    from ai_hats.dry_run import _exclusive_rebuild
    from ai_hats.materialization import ApplyMaterializer

    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)
    lock_path = cache_mat.parent / f"{cache_mat.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    port = ApplyMaterializer(lock_timeout=0.1)
    with filelock.FileLock(str(lock_path)):
        with pytest.raises(RuntimeError, match="materialization blocked"):
            with _exclusive_rebuild(cache_mat, port, materialize=True):
                pass


def test_the_materializing_entry_point_goes_through_the_lock(project: Path):
    """Wires the unit above to the entry point, without waiting on a timeout.

    Taking the lock creates the file beside the cache dir, so its presence after
    a clean run is the proof that the rebuild was serialised at all.
    """
    dry_run_hitl(project, provider="claude", materialize=True)

    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)
    assert (cache_mat.parent / f"{cache_mat.name}.lock").exists()


def test_a_held_lock_does_not_stall_a_plain_dry_run(project: Path):
    """The default path writes nothing, so it has nothing to serialise against."""
    import filelock

    cache_mat = session_cache_dir(project, DRY_RUN_MATERIALIZE_SESSION_ID)
    lock_path = cache_mat.parent / f"{cache_mat.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with filelock.FileLock(str(lock_path)):
        report = dry_run_hitl(project, provider="claude", materialize=False)

    assert report.plan.entries


def test_dry_run_materialize_does_not_affect_subsequent_default_dry_run(project: Path):
    """S6 / R3: --materialize followed by default --dry-run leaves default report unchanged."""
    report_clean = dry_run_hitl(project, provider="claude", materialize=False)
    entries_clean = [
        (e.kind.value, str(e.target), e.size, e.digest) for e in report_clean.plan.entries
    ]

    # Run --materialize
    dry_run_hitl(project, provider="claude", materialize=True)

    # Run default dry-run again
    report_after = dry_run_hitl(project, provider="claude", materialize=False)
    entries_after = [
        (e.kind.value, str(e.target), e.size, e.digest) for e in report_after.plan.entries
    ]

    assert entries_clean == entries_after
