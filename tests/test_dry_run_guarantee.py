"""The guarantee: a dry-run leaves the filesystem byte-identical (HATS-1211 §2).

This is the check that makes escaping the port impossible to do quietly — a
write that goes around it happens for real during a dry-run and shows up here.
Run per (surface × run_mode); the AUTOMATE pairs are where HATS-1207's bypasses
live, so they are asserted to REPORT the escape rather than to be clean.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_hats.dry_run import dry_run_automate, dry_run_hitl

SURFACES = ["claude", "agy", "cline"]


def _fingerprint(root: Path) -> dict[str, str]:
    """Path -> content digest for every file under ``root``."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project rooted in tmp_path, isolated from the user's home."""
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


@pytest.mark.parametrize("surface", SURFACES)
def test_hitl_dry_run_leaves_the_filesystem_byte_identical(project: Path, surface: str):
    before = _fingerprint(project)

    report = dry_run_hitl(project, provider=surface)

    assert _fingerprint(project) == before
    assert report.escapes == ()
    assert report.plan.entries, "a dry-run that plans nothing is not a dry-run"


@pytest.mark.parametrize("surface", SURFACES)
def test_automate_dry_run_leaves_the_filesystem_byte_identical(
    project: Path, surface: str
):
    """Escapes are undone, so the fs is clean either way — that is the promise."""
    before = _fingerprint(project)

    dry_run_automate(project, provider=surface, task="demo")

    assert _fingerprint(project) == before


@pytest.mark.parametrize("surface", ["agy", "cline"])
def test_automate_reports_the_runner_bypass_it_traverses(project: Path, surface: str):
    """HATS-1207 bypass 2: materialize_runtime_skills takes no port, so it writes.

    The dry-run must SAY so rather than quietly produce a clean-looking report.
    Turning green here is HATS-1207's job — then this assertion inverts.
    """
    report = dry_run_automate(project, provider=surface, task="demo")

    assert any("bypass 2" in n for n in report.notes)


def test_claude_automate_reports_that_the_engine_recomputes(project: Path):
    """HATS-1207 bypass 1: the values shown are the builder's, not the SDK's."""
    report = dry_run_automate(project, provider="claude", task="demo")

    assert any("bypass 1" in n for n in report.notes)
