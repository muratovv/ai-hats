"""``--dry-run --materialize`` applies the plan to the fixed dry-run root (HATS-1551)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.session_artifacts import RunMode, assemble_brief
from ai_hats.session_plan import DRY_RUN_MATERIALIZE_SESSION_ID, DRY_RUN_SESSION_ID, preview


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


def _hitl(project: Path, *, materialize: bool):
    return preview(
        ProjectLayout.at(project),
        role=None,
        provider="claude",
        run_mode=RunMode.HITL,
        materialize=materialize,
    )


def _rows(record: dict) -> list[tuple]:
    return [(e["kind"], e["target"], e["size"], e["digest"]) for e in record["materialized"]]


def test_dry_run_materialize_leaves_tree_on_disk(project: Path):
    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)
    assert not cache_mat.exists()

    shown = _hitl(project, materialize=True)

    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert shown.record["materialized"]
    # What application did rides the record (ADR-0036 D3), row by row.
    assert {e["outcome"] for e in shown.record["materialized"]} == {"written"}
    # S3 / R6 & R7: the notes state the tree's location and the synthetic sid.
    assert any("materialized session tree written to disk at" in n for n in shown.record["notes"])
    assert any(
        f"synthetic session_id '{DRY_RUN_MATERIALIZE_SESSION_ID}'" in n
        for n in shown.record["notes"]
    )


def test_dry_run_automate_materialize_leaves_tree_on_disk(project: Path):
    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)
    assert not cache_mat.exists()
    layout = ProjectLayout.at(project)

    shown = preview(
        layout,
        role=None,
        provider="claude",
        run_mode=RunMode.AUTOMATE,
        brief=assemble_brief(layout, task="test task", ticket_id=""),
        materialize=True,
    )

    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert shown.record["materialized"]
    assert any("materialized session tree written to disk at" in n for n in shown.record["notes"])


def test_dry_run_default_leaves_nothing_on_disk(project: Path):
    cache_std = ProjectLayout.at(project).cache.session(DRY_RUN_SESSION_ID)
    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)

    shown = _hitl(project, materialize=False)

    assert not cache_std.exists()
    assert not cache_mat.exists()
    assert "outcome" not in shown.record["materialized"][0], "nothing was applied"


def test_dry_run_materialize_determinism_on_repeated_runs(project: Path):
    """S5 / R4: two --materialize runs in a row yield identical plan entries and files."""
    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)

    first = _hitl(project, materialize=True)
    files1 = {str(p): p.read_bytes() for p in cache_mat.rglob("*") if p.is_file()}

    second = _hitl(project, materialize=True)
    files2 = {str(p): p.read_bytes() for p in cache_mat.rglob("*") if p.is_file()}

    assert _rows(first.record) == _rows(second.record)
    assert files1 == files2


def test_a_held_lock_stalls_the_materializing_entry_point(project: Path):
    """A fixed sid means one directory for every run, so application is locked.

    HATS-1248 dropped the skills-mirror lock because a sid-keyed directory has
    exactly one writer. ``--materialize`` pins the sid, which brings the second
    writer back; ``apply`` serialises on the lock beside the root, and waiting is
    the property itself. Whether the lock file outlives its release is filelock's
    cleanup policy, not evidence of exclusion. The window is cut from an unlocked
    run on this machine, so a slow host widens it rather than passing vacuously.
    """  # comment-length: allow — why the file's presence proves nothing
    import threading
    from time import perf_counter

    import filelock

    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)
    lock_path = cache_mat.parent / f"{cache_mat.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    started = perf_counter()
    _hitl(project, materialize=True)
    window = max(0.25, 5 * (perf_counter() - started))

    done = threading.Event()
    failure: list[Exception] = []

    def rebuild() -> None:
        try:
            _hitl(project, materialize=True)
        except Exception as exc:
            failure.append(exc)  # re-raised in the main thread, below
        finally:
            done.set()

    worker = threading.Thread(target=rebuild, daemon=True)
    with filelock.FileLock(str(lock_path)):
        worker.start()
        assert not done.wait(window), "the rebuild ran while the lock was held"
    assert done.wait(30), "the rebuild never finished after the lock was released"
    if failure:
        raise failure[0]


def test_a_held_lock_does_not_stall_a_plain_dry_run(project: Path):
    """The default path writes nothing, so it has nothing to serialise against."""
    import filelock

    cache_mat = ProjectLayout.at(project).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)
    lock_path = cache_mat.parent / f"{cache_mat.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with filelock.FileLock(str(lock_path)):
        shown = _hitl(project, materialize=False)

    assert shown.record["materialized"]


def test_dry_run_materialize_does_not_affect_subsequent_default_dry_run(project: Path):
    """S6 / R3: --materialize followed by a default --dry-run leaves the plan unchanged."""
    clean = _rows(_hitl(project, materialize=False).record)

    _hitl(project, materialize=True)

    assert _rows(_hitl(project, materialize=False).record) == clean
