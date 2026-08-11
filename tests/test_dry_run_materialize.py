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
    assert any(f"synthetic session_id '{DRY_RUN_MATERIALIZE_SESSION_ID}'" in n for n in report.notes)


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
