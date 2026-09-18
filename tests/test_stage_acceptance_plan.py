"""The stage's exit condition on every surface that plans (ADR-0036 D2, D3):
two plannings of one input are equal, a second application reaches no write
primitive, and a changed input shows — on the real ``maintainer`` composition."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import plan_session, plans, probe_host
from ai_hats.surface_registry import get_surface, surface_names
from ai_hats.surfaces import apply
from ai_hats.surfaces.plan import Prompt, PromptBlock, PromptMember

PLANNING = [name for name in surface_names() if plans(get_surface(name))]
MODES = [RunMode.HITL, RunMode.AUTOMATE]

#: Where a reworded prompt lands, per surface and mode: a context file, a merged
#: config document, or nowhere on disk when it rides the launch inline. A
#: surface joining the planners declares which it is in each mode.
REWORDED_PROMPT_WRITES = {
    ("claude", RunMode.HITL): ["write_bytes"],
    ("claude", RunMode.AUTOMATE): ["write_bytes"],
    ("codex", RunMode.HITL): [],
    ("codex", RunMode.AUTOMATE): [],
    ("opencode", RunMode.HITL): ["write_text"],
    ("opencode", RunMode.AUTOMATE): ["write_text"],
    ("agy", RunMode.HITL): ["write_bytes"],
    ("agy", RunMode.AUTOMATE): [],
    ("cline", RunMode.HITL): [],
    ("cline", RunMode.AUTOMATE): [],
}


@pytest.fixture
def maintainer(tmp_path: Path, monkeypatch):
    """Function-scoped: a module-scoped assembler would be built before the
    per-test user-home isolation and read the developer's roots."""
    from ai_hats.assembler import Assembler
    from ai_hats.materialize import compose_to_run
    from ai_hats.surfaces import adapt

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("AI_HATS_CODEX_BASE_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    (tmp_path / "config-home" / "opencode").mkdir(parents=True)
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # agy registers its dispatcher in the person's settings — a home of the test's own.
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini-home"))
    project = tmp_path / "proj"
    project.mkdir()
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    composition = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    return asm, composition


def _plan(asm, composition, surface_name: str, root: Path, run_mode: RunMode):
    """The session's plan; for a surface that cannot wrap commands the
    surface's own — the consent layer refuses such a HITL launch outright
    (``test_a_surface_without_command_wrappers_refuses_a_consent_role_at_planning``)
    and the planner underneath is what this file holds to the contract."""
    surface = get_surface(surface_name)
    host = probe_host(surface=surface)
    if run_mode is RunMode.HITL and not surface.supports_session_command_wrappers():
        return surface.plan(
            composition,
            run_mode=run_mode,
            policy=SessionPolicy(),
            root=root,
            layout=asm.layout,
            host=host,
        )
    return plan_session(
        composition,
        surface,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=host,
    )


def test_every_surface_plans():
    assert set(PLANNING) == set(surface_names()), (
        "every registered surface plans its session — cline was the fifth and last"
    )
    assert set(PLANNING) >= {"claude", "codex", "opencode", "agy", "cline"}
    declared = {surface for surface, _mode in REWORDED_PROMPT_WRITES}
    assert set(PLANNING) <= declared, "a surface that plans declares where a reworded prompt lands"
    assert all((surface, mode) in REWORDED_PROMPT_WRITES for surface in PLANNING for mode in MODES)


def test_a_surface_without_command_wrappers_refuses_a_consent_role_at_planning(
    maintainer, tmp_path: Path
):
    """agy inherits no session PATH, so a role declaring consent is refused
    before anything is written (ADR-0036 D2) — as the launch refused it; the
    sub-agent path carries no wrapper and plans."""
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    surface = get_surface("agy")
    assert not surface.supports_session_command_wrappers()
    assert any(h.app == "consent_gate" for h in composition.hooks.external), "the sample declares"
    with pytest.raises(RuntimeError, match="cannot enforce role-declared command consent"):
        plan_session(
            composition,
            surface,
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=root,
            layout=asm.layout,
            host=probe_host(surface=surface),
        )
    assert plan_session(
        composition,
        surface,
        run_mode=RunMode.AUTOMATE,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(surface=surface),
    ).entries


@pytest.mark.parametrize("run_mode", MODES)
@pytest.mark.parametrize("surface", PLANNING)
def test_two_plannings_of_one_input_are_equal_before_and_after_application(
    maintainer, tmp_path: Path, surface: str, run_mode: RunMode
):
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"

    before = _plan(asm, composition, surface, root, run_mode)
    apply(before)
    after = _plan(asm, composition, surface, root, run_mode)

    assert before == after and before.digest == after.digest
    assert len(before.entries) > 40, "the sample composes dozens of skills"
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _plan(asm, more, surface, root, run_mode) != before, "a changed input must show"


@pytest.mark.parametrize("run_mode", MODES)
@pytest.mark.parametrize("surface", PLANNING)
def test_applying_the_plan_twice_reaches_no_primitive(
    maintainer, tmp_path: Path, writes: list[str], surface: str, run_mode: RunMode
):
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    plan = _plan(asm, composition, surface, root, run_mode)
    apply(plan)
    writes.clear()
    assert apply(plan).changed is False
    assert writes == []

    mirrored = next(
        e for e in plan.entries if e.kind is WriteKind.COPY_TREE and e.target.name == "safety-guard"
    )
    gate = mirrored.target / "hooks" / "safety_gate.py"
    gate.unlink()  # safe-delete: ok a session mirror the test owns
    writes.clear()
    assert apply(plan).changed is True and gate.is_file(), "the mirror heals by re-application"
    assert writes == ["copy2"]

    reworded = dataclasses.replace(
        composition,
        prompt=Prompt(
            (
                PromptBlock(None, (PromptMember("maintainer::prompt", "# other\n", None),)),
                *composition.prompt.blocks[1:],
            )
        ),
    )
    other = _plan(asm, reworded, surface, root, run_mode)
    assert other != plan
    writes.clear()
    apply(other)
    assert writes == REWORDED_PROMPT_WRITES[surface, run_mode], (
        "one changed prompt, its one delivery"
    )
