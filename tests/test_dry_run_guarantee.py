"""The guarantee: a dry-run leaves the filesystem byte-identical (HATS-1211 §2).

This is the check that makes escaping the port impossible to do quietly — a
write that goes around it happens for real during a dry-run and shows up here.
Run per (surface × run_mode). The AUTOMATE pairs used to be where HATS-1207's
bypasses lived and were asserted to REPORT an escape; since HATS-1207 routed
both run-paths through the builder they are asserted to be clean instead.

Files only — read as "a dry-run does nothing" this file overstates itself, which
is how a socket bind hid inside ``ClineSurface.get_env``. The non-file half is
``tests/test_dry_run_claims_nothing.py`` (HATS-1554).
"""  # comment-length: allow — what the guarantee does NOT cover is the point

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import hashlib
from pathlib import Path

import pytest

from ai_hats.dry_run import dry_run_automate, dry_run_hitl

SURFACES = ["claude", "agy", "cline"]


def _fingerprint(project: Path) -> dict[str, str]:
    """Path -> content digest across every root a dry-run could write to.

    HATS-1398 moved the cache out of the project, so walking the project alone
    would pass while a build wrote freely to the real target — the guarantee has
    to cover the cache root too, or it only proves the empty half.
    """

    out: dict[str, str] = {}
    for root in (project, ProjectLayout.at(project).cache.root):
        for p in sorted(root.rglob("*")) if root.is_dir() else ():
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
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

    report = dry_run_hitl(ProjectLayout.at(project), provider=surface)

    assert _fingerprint(project) == before
    assert report.escapes == ()
    assert report.plan.entries, "a dry-run that plans nothing is not a dry-run"


@pytest.mark.parametrize("surface", SURFACES)
def test_automate_dry_run_leaves_the_filesystem_byte_identical(project: Path, surface: str):
    """Escapes are undone, so the fs is clean either way — that is the promise."""
    before = _fingerprint(project)

    dry_run_automate(ProjectLayout.at(project), provider=surface, task="demo")

    assert _fingerprint(project) == before


@pytest.mark.parametrize("surface", ["agy", "cline"])
def test_automate_no_longer_traverses_the_runner_bypass(project: Path, surface: str):
    """HATS-1207 S4 closed bypass 2 — this is the promised inversion.

    Before: the runner composed the role itself via ``materialize_runtime_skills``,
    which takes no port and therefore wrote for real. Now the role reaches the
    sub-agent through the builder, so there is nothing to warn about and nothing
    escapes: an empty ``escapes`` is the load-bearing half of this assertion.
    """
    report = dry_run_automate(ProjectLayout.at(project), provider=surface, task="demo")

    assert not any("bypass 2" in n for n in report.notes)
    assert report.escapes == ()
    assert "Role body." in " ".join(report.launch)


def test_claude_automate_delivers_the_builders_own_values(project: Path):
    """HATS-1207 S3 closed bypass 1 — the SDK now receives what the builder built."""
    report = dry_run_automate(ProjectLayout.at(project), provider="claude", task="demo")

    assert not any("bypass 1" in n for n in report.notes)
    assert report.escapes == ()
    assert any(arg.startswith("system_prompt=") for arg in report.launch)


@pytest.mark.parametrize("surface", SURFACES)
def test_the_reported_env_names_what_ai_hats_adds_to_the_child(project: Path, surface: str):
    """HATS-1548: the record listed two of the six sources the launch merges.

    ``AI_HATS_SESSION_ID`` is the load-bearing one — it is what
    ``check_resolve.session_id()`` reads to pick between resolving off the
    session mirror and resolving live, so a report that omits it cannot be used
    to reason about a gate at all.
    """
    from ai_hats.constants import ENV_ROLE, ENV_ROOT_PID
    from ai_hats_observe.trace import ENV_SESSION_ID

    report = dry_run_hitl(ProjectLayout.at(project), provider=surface)

    assert {ENV_SESSION_ID, ENV_ROLE, ENV_ROOT_PID, "TRACE_LOG_PATH"} <= set(report.env)
    assert report.to_dict()["env_keys"] == sorted(report.env)
    assert "PATH" not in report.env, "inherited os.environ is not what the launch adds"


@pytest.mark.parametrize("surface", ["claude", "agy"])
def test_full_render_shows_the_composed_body(project: Path, surface: str):
    """The wiring, not the rendering: ``dry_run_hitl`` must hand the body over.

    Its own render-level test builds the report by hand, so it stayed green with
    the report field never populated — found by reverting the wiring (HATS-1548).

    cline is excluded on purpose; see the sibling below.
    """
    report = dry_run_hitl(ProjectLayout.at(project), provider=surface)

    assert report.prompt is not None, "this surface writes a prompt file"
    assert "Role body." in report.render(full=True)
    assert "(not written)" not in report.render(full=True)


def test_cline_hitl_has_no_prompt_file_to_dump(project: Path):
    """Why cline sits out the case above — and pinned so it cannot rot.

    It materializes no ``.md``, so ``full=True`` renders no body section at all.
    Parametrizing it in would have passed on the role text appearing in the
    launch argv instead, which is a different claim entirely.
    """
    report = dry_run_hitl(ProjectLayout.at(project), provider="cline")

    assert report.prompt is None
    assert "Role body." in " ".join(report.launch), "it rides the argv instead"
