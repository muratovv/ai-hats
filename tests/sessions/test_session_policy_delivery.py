"""A non-default SessionPolicy is honoured at the delivery boundary (HATS-1207).

Acceptance (b): for every (surface, run_mode) pair, a policy passed into the
build is observable in what the session would actually receive — no path
recomputes or re-materializes a suppressed category behind the builder's back.

Asserted at the boundary, never on the policy object: ``SessionPolicy() ==
SessionPolicy()`` is a tautology, and it was the failure mode called out in the
HATS-1167 plan review. Tokens are compared exactly rather than by substring —
``"-s" in arg`` also matches a project path containing ``-s``.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import SessionPolicy

SURFACES = ["claude", "agy", "cline"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
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


# HITL: the role must leave the launch command, per surface's own delivery flag.
_HITL_CONTEXT_FLAG = {
    "claude": "--system-prompt-file",
    "agy": "--add-dir",
    "cline": "-s",
}


@pytest.mark.parametrize("surface", SURFACES)
def test_hitl_context_false_drops_the_role_flag(project: Path, surface: str):
    on = dry_run_hitl(
        ProjectLayout.at(project), role="test-role", provider=surface, policy=SessionPolicy()
    )
    off = dry_run_hitl(
        ProjectLayout.at(project),
        role="test-role",
        provider=surface,
        policy=SessionPolicy(context=False),
    )

    flag = _HITL_CONTEXT_FLAG[surface]
    assert flag in on.launch, "baseline: the flag is there under the default policy"
    assert flag not in off.launch


def test_hitl_context_false_keeps_cline_interactive(project: Path):
    """M5 regression: -i is launch mode, not context — suppressing one must not drop the other."""
    off = dry_run_hitl(
        ProjectLayout.at(project),
        role="test-role",
        provider="cline",
        policy=SessionPolicy(context=False),
    )

    assert "-i" in off.launch


def test_hitl_context_false_writes_no_gemini_md(project: Path):
    off = dry_run_hitl(
        ProjectLayout.at(project),
        role="test-role",
        provider="agy",
        policy=SessionPolicy(context=False),
    )

    assert not any(e.target.name == "GEMINI.md" for e in off.plan.entries)


@pytest.mark.parametrize("surface", ["agy", "cline"])
def test_automate_context_false_drops_the_role_sections(project: Path, surface: str):
    on = dry_run_automate(
        ProjectLayout.at(project),
        role="test-role",
        provider=surface,
        task="demo",
        policy=SessionPolicy(),
    )
    off = dry_run_automate(
        ProjectLayout.at(project),
        role="test-role",
        provider=surface,
        task="demo",
        policy=SessionPolicy(context=False),
    )

    assert any("Role body." in arg for arg in on.launch), "baseline"
    assert not any("Role body." in arg for arg in off.launch)


def test_claude_automate_context_false_carries_no_role_text(project: Path):
    off = dry_run_automate(
        ProjectLayout.at(project),
        role="test-role",
        provider="claude",
        task="demo",
        policy=SessionPolicy(context=False),
    )

    assert not any(arg.startswith("system_prompt=") for arg in off.launch)


def test_claude_automate_hooks_false_passes_no_settings(project: Path):
    off = dry_run_automate(
        ProjectLayout.at(project),
        role="test-role",
        provider="claude",
        task="demo",
        policy=SessionPolicy(hooks=False),
    )

    assert not any(arg.startswith("settings=") for arg in off.launch)
