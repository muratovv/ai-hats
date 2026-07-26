"""The guarantee: a dry-run leaves the filesystem byte-identical (HATS-1211 §2).

This is the check that makes escaping the port impossible to do quietly — a
write that goes around it happens for real during a dry-run and shows up here.
Run per (surface × run_mode). The AUTOMATE pairs used to be where HATS-1207's
bypasses lived and were asserted to REPORT an escape; since HATS-1207 routed
both run-paths through the builder they are asserted to be clean instead.
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
def test_automate_no_longer_traverses_the_runner_bypass(project: Path, surface: str):
    """HATS-1207 S4 closed bypass 2 — this is the promised inversion.

    Before: the runner composed the role itself via ``materialize_runtime_skills``,
    which takes no port and therefore wrote for real. Now the role reaches the
    sub-agent through the builder, so there is nothing to warn about and nothing
    escapes: an empty ``escapes`` is the load-bearing half of this assertion.
    """
    report = dry_run_automate(project, provider=surface, task="demo")

    assert not any("bypass 2" in n for n in report.notes)
    assert report.escapes == ()
    assert "# SYSTEM_ROLE" in " ".join(report.launch)


def test_claude_automate_delivers_the_builders_own_values(project: Path):
    """HATS-1207 S3 closed bypass 1 — the SDK now receives what the builder built."""
    report = dry_run_automate(project, provider="claude", task="demo")

    assert not any("bypass 1" in n for n in report.notes)
    assert report.escapes == ()
    assert any(arg.startswith("system_prompt=") for arg in report.launch)
