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

#: Where a reworded prompt lands: the context file for a surface that writes
#: one, nowhere on disk for one that hands the prompt inline (the launch shows
#: it instead). A surface joining the planners declares which it is.
REWORDED_PROMPT_WRITES = {"claude": ["write_bytes"], "codex": []}


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
    surface = get_surface(surface_name)
    return plan_session(
        composition,
        surface,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(surface=surface),
    )


def test_every_surface_plans():
    assert set(PLANNING) >= {"claude", "codex"}
    assert set(PLANNING) <= set(REWORDED_PROMPT_WRITES), (
        "a surface that plans declares where a reworded prompt lands"
    )


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
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


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
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
    assert writes == REWORDED_PROMPT_WRITES[surface], "one changed prompt, its one delivery"
