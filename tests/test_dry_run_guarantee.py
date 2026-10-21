"""The guarantee: a dry-run leaves the filesystem byte-identical (HATS-1211 §2).

The dry-run is a projection of the plan (ADR-0036 D5): it plans for a stub
root and reaches no write primitive. Run per (surface × run_mode). Files only —
read as "a dry-run does nothing" this file overstates itself, which is how a
socket bind hid inside ``ClineSurface.get_env``. The non-file half is
``tests/test_dry_run_claims_nothing.py`` (HATS-1554).
"""  # comment-length: allow — what the guarantee does NOT cover is the point

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.session_artifacts import RunMode, assemble_brief
from ai_hats.session_plan import preview, render_record
from ai_hats.surface_registry import surface_names

SURFACES = sorted(surface_names())


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
    """A composable project rooted in tmp_path, isolated from the user's home;
    an empty Codex home stands where that surface insists on one."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    for name in ("AI_HATS_CODEX_BASE_HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "GEMINI_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)

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


def _hitl(project: Path, surface: str):
    return preview(ProjectLayout.at(project), role=None, provider=surface, run_mode=RunMode.HITL)


def _automate(project: Path, surface: str):
    layout = ProjectLayout.at(project)
    return preview(
        layout,
        role=None,
        provider=surface,
        run_mode=RunMode.AUTOMATE,
        brief=assemble_brief(layout, task="demo", ticket_id=""),
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_hitl_dry_run_leaves_the_filesystem_byte_identical(
    project: Path, writes: list[str], surface: str
):
    before = _fingerprint(project)
    writes.clear()

    shown = _hitl(project, surface)

    assert _fingerprint(project) == before
    assert writes == [], "a dry-run reaches no write primitive"
    assert shown.record["materialized"], "a dry-run that plans nothing is not a dry-run"


@pytest.mark.parametrize("surface", SURFACES)
def test_automate_dry_run_leaves_the_filesystem_byte_identical(
    project: Path, writes: list[str], surface: str
):
    before = _fingerprint(project)
    writes.clear()

    _automate(project, surface)

    assert _fingerprint(project) == before
    assert writes == []


@pytest.mark.parametrize("surface", ["agy", "cline"])
def test_automate_hands_the_role_to_a_cli_sub_agent_in_its_prompt_token(
    project: Path, surface: str
):
    """The role reaches a CLI sub-agent through the plan's prompt, in the argv."""
    shown = _automate(project, surface)

    assert "Role body." in " ".join(shown.record["launch"])


def test_claude_automate_delivers_the_plans_own_values(project: Path):
    """The SDK receives the option document the plan built."""
    shown = _automate(project, "claude")

    assert any(arg.startswith("system_prompt=") for arg in shown.record["launch"])


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

    env_keys = _hitl(project, surface).record["env_keys"]

    assert {ENV_SESSION_ID, ENV_ROLE, ENV_ROOT_PID, "TRACE_LOG_PATH"} <= set(env_keys)
    assert env_keys == sorted(env_keys)
    assert "PATH" not in env_keys, "inherited os.environ is not what the launch adds"


@pytest.mark.parametrize("surface", ["claude", "agy"])
def test_full_render_shows_the_composed_body(project: Path, surface: str):
    """The wiring, not the rendering: ``preview`` must hand the body over.

    The render-level test builds the record by hand, so it would stay green
    with the body never handed over — found by reverting the wiring (HATS-1548).

    cline is excluded on purpose; see the sibling below.
    """
    shown = _hitl(project, surface)

    assert shown.record["prompt"] is not None, "this surface writes a prompt file"
    text = render_record(shown.record, full=True, prompt_text=shown.prompt)
    assert "Role body." in text
    assert "(not written)" not in text


def test_cline_hitl_has_no_prompt_file_to_dump(project: Path):
    """Why cline sits out the case above — and pinned so it cannot rot.

    It writes no context file, so ``full=True`` renders no body section at all.
    Parametrizing it in would have passed on the role text appearing in the
    launch argv instead, which is a different claim entirely.
    """
    shown = _hitl(project, "cline")

    assert shown.record["prompt"] is None
    assert "Role body." in " ".join(shown.record["launch"]), "it rides the argv instead"
    assert "Role body." in shown.prompt, "and the bytes still ride beside the record"


def test_the_dry_run_carries_the_composition_half_by_kind(project: Path):
    """What the prompt never shows — hooks by kind and consent ends — is in the
    report (ADR-0036 D5; the blindness that let an unarmed role close a card)."""
    shown = _hitl(project, "claude")

    composition = shown.record["composition"]
    assert composition["identity"] == "test-role"
    assert [s["name"] for s in composition["skills"]] == ["skills::s"]
    assert composition["hooks"] == {"runtime": [], "external": []}
    assert "\ncomposition  test-role  digest=" in render_record(shown.record)
