"""The composition half of the materialization plan, adapted from today's path.

Real library, real ``maintainer``: the names are the flat model's, the text is
what ``show-prompt`` prints today, every absence is ``None``, and two adapts of
one composition are ``==`` (ADR-0036 D1, D7).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.config.overlay import OverlayConfig
from ai_hats.materialize import compose_to_run
from ai_hats.surface_registry import get_surface
from ai_hats.surfaces import adapt
from ai_hats.surfaces.plan import (
    BODY_BLOCK,
    AppPoint,
    ConsentHook,
    OnError,
    TraceEntry,
)


@pytest.fixture
def maintainer(tmp_path: Path):
    """Function-scoped on purpose: a module-scoped assembler would be built
    before the per-test user-home isolation and read the developer's roots."""
    project = tmp_path / "proj"
    project.mkdir()
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    plan = adapt(result, identity="maintainer", resolver=asm.resolver, overlays=())
    return asm, result, plan


def test_prompt_text_is_what_show_prompt_prints(maintainer):
    _asm, result, plan = maintainer
    assert plan.prompt.text == get_surface("claude").build_system_prompt(result)


def test_prompt_blocks_name_the_sections_in_text_order(maintainer):
    _asm, _result, plan = maintainer
    blocks = {b.name: b.members for b in plan.prompt.blocks}
    assert [b.name for b in plan.prompt.blocks] == ["PRIORITIES", BODY_BLOCK, "RULES"]
    assert blocks["PRIORITIES"] == ("maintainer::priorities",)
    assert "trait-agent::prompt" in blocks[BODY_BLOCK]
    assert blocks[BODY_BLOCK][-1] == "maintainer::prompt", "the role's own text comes last"
    assert "rules::rule_backlog_discipline" in blocks["RULES"]
    for name in blocks["RULES"]:
        assert f"### {name.removeprefix('rules::')}\n" in plan.prompt.text


def test_skills_carry_full_names_paths_and_tree_digests(maintainer):
    from ai_hats.fs_digest import dir_digest

    _asm, result, plan = maintainer
    assert [s.name for s in plan.skills] == [f"skills::{s.name}" for s in result.skills]
    hatrack = next(s for s in plan.skills if s.name == "skills::hatrack")
    source = next(s.source_path for s in result.skills if s.name == "hatrack")
    assert hatrack.path == source.resolve() and hatrack.path.is_absolute()
    assert hatrack.content_digest == dir_digest(source)
    elsewhere = dataclasses.replace(hatrack, path=Path("/elsewhere/hatrack"))
    assert elsewhere.digest != hatrack.digest, "one tree at two paths is two skills"


def test_hooks_by_kind_each_with_its_payload(maintainer):
    _asm, result, plan = maintainer
    gate = next(s.source_path for s in result.skills if s.name == "quality-gate")
    points = [(h.point, h.payload.path, h.on_error) for h in plan.hooks.workflow]
    assert points == [
        (AppPoint("rack", "tasks", "->review"), gate / "hooks" / "review-gate.sh", OnError.REFUSE),
        (AppPoint("rack", "tasks", "->done"), gate / "hooks" / "done-gate.sh", OnError.REFUSE),
        (AppPoint("wt", None, "pre-merge"), gate / "hooks" / "merge-gate.sh", OnError.REFUSE),
    ]
    assert ("pre-push", gate / "git_hooks" / "pre-push-e2e-master.sh") in [
        (h.at, h.payload.path) for h in plan.hooks.git
    ]
    guard = [h for h in plan.hooks.runtime if h.payload.path.name == "safety_gate.py"]
    assert [h.at for h in guard] == ["PreToolUse"]
    assert all(h.payload.path.is_absolute() for h in plan.hooks.git + plan.hooks.runtime)
    venv = [h for h in plan.hooks.worktree if h.payload.path.name == "provision-venv.sh"]
    assert [(h.at, h.on) for h in venv] == [("wt_in", None)]
    assert len(guard[0].payload.content_digest) == 64


def test_consent_is_a_hook_kind_carrying_the_parsed_ends(maintainer):
    _asm, _result, plan = maintainer
    consent = plan.hooks.consent
    assert (
        ConsentHook("rack.transition", "plan->execute", "plan", "execute", "trait-agent", None)
        in consent
    )
    assert ConsentHook("rack.transition", "->done", None, "done", "trait-agent", None) in consent
    assert ConsentHook("wt.merge", "pre-merge", None, None, "trait-agent", None) in consent


def test_trace_attributes_each_term_to_its_bringer(maintainer):
    _asm, _result, plan = maintainer
    assert TraceEntry("trait-agent", "maintainer", None) in plan.trace
    assert TraceEntry("rules::rule_backlog_discipline", "trait-agent", None) in plan.trace
    assert TraceEntry("skills::quality-gate", "ai-hats-dev", None) in plan.trace
    assert TraceEntry("ai-hats-gates", "maintainer", None) in plan.trace


def test_trace_names_the_override_that_added_or_removed_a_term(maintainer):
    asm, _result, _plan = maintainer
    overlay = OverlayConfig(
        add_traits=["worker"], remove_traits=["ai-hats-gates"], remove_skills=["hatrack"]
    )
    result = compose_to_run(asm, "maintainer", runtime_overlay=overlay)
    plan = adapt(
        result, identity="maintainer", resolver=asm.resolver, overlays=[(overlay, "project")]
    )
    assert TraceEntry("worker", "overrides::project", None) in plan.trace
    assert TraceEntry("ai-hats-gates", "maintainer", "overrides::project") in plan.trace
    assert TraceEntry("skills::hatrack", "trait-agent", "overrides::project") in plan.trace
    assert "skills::hatrack" not in [s.name for s in plan.skills]


def _optional(annotation: str) -> bool:
    return "| None" in annotation


def _walk(value, path: str, findings: list[str]) -> None:
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            child = getattr(value, field.name)
            here = f"{path}.{field.name}"
            annotation = str(field.type)
            if _optional(annotation):
                if child is not None and child in ("", (), [], {}, 0):
                    findings.append(f"{here} = {child!r} where absence must be None")
            elif isinstance(child, str) and child == "":
                findings.append(f"{here} = '' on a required field")
            _walk(child, here, findings)
    elif isinstance(value, (tuple, list)):
        for i, item in enumerate(value):
            _walk(item, f"{path}[{i}]", findings)
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{path}[{key!r}]", findings)


def test_every_absence_is_none_never_an_empty_value(maintainer):
    _asm, _result, plan = maintainer
    findings: list[str] = []
    _walk(plan, "plan", findings)
    assert findings == []


def test_two_adapts_of_one_composition_are_equal_and_a_changed_input_is_not(maintainer):
    asm, result, plan = maintainer
    again = adapt(result, identity="maintainer", resolver=asm.resolver, overlays=())
    assert again == plan and again.digest == plan.digest
    extra = dataclasses.replace(result, skills=[*result.skills, result.skills[0]])
    changed = adapt(extra, identity="maintainer", resolver=asm.resolver, overlays=())
    assert changed != plan and changed.digest != plan.digest


def test_the_plan_digest_folds_every_record_down_the_tree(maintainer):
    _asm, _result, plan = maintainer
    payload = plan.hooks.runtime[0].payload
    other = dataclasses.replace(payload, content_digest="0" * 64)
    hooks = dataclasses.replace(
        plan.hooks,
        runtime=(
            dataclasses.replace(plan.hooks.runtime[0], payload=other),
            *plan.hooks.runtime[1:],
        ),
    )
    assert other.digest != payload.digest
    assert hooks.digest != plan.hooks.digest
    assert dataclasses.replace(plan, hooks=hooks).digest != plan.digest


def test_the_funnel_walk_finds_an_empty_string_posing_as_absence():
    findings: list[str] = []
    _walk(ConsentHook("rack.transition", "->done", "", "done", "trait-agent", None), "c", findings)
    assert findings == ["c.source = '' where absence must be None"]


def test_a_declared_payload_that_is_missing_is_a_diagnostic_and_no_hook(tmp_path: Path):
    from ai_hats.diagnostics import Level
    from ai_hats.resolver import LibraryResolver
    from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

    skill = tmp_path / "skills" / "guard"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guard\nai_hats:\n  runtime_hooks:\n    PreToolUse:\n"
        "      - matcher: Bash\n        script: hooks/gone.sh\n---\n# guard\n"
    )
    (tmp_path / "roles" / "r").mkdir(parents=True)
    (tmp_path / "roles" / "r" / "config.yaml").write_text(
        "name: r\ncomposition:\n  skills: [guard]\n"
    )
    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[ResolvedComponent("guard", ComponentKind.SKILL, skill)],
        injections=[],
    )
    plan = adapt(result, identity="r", resolver=LibraryResolver([tmp_path]), overlays=())
    assert plan.hooks.runtime == ()
    assert [d.level for d in plan.diagnostics] == [Level.WARN]
    assert "skills::guard" in plan.diagnostics[0].message
    assert "hooks/gone.sh" in plan.diagnostics[0].message


def test_trace_reads_a_same_layer_remove_and_add_as_the_composer_does(maintainer):
    """``remove: [X]`` + ``add: [X]`` in one layer is the move-to-end reorder,
    and X stays composed — the trace must hold one row for it, not two."""
    asm, _result, _plan = maintainer
    rule = "rule_backlog_discipline"
    overlay = OverlayConfig(add_rules=[rule], remove_rules=[rule])
    result = compose_to_run(asm, "maintainer", runtime_overlay=overlay)
    plan = adapt(
        result, identity="maintainer", resolver=asm.resolver, overlays=[(overlay, "project")]
    )
    rows = [t for t in plan.trace if t.term == f"rules::{rule}"]
    assert rows == [TraceEntry(f"rules::{rule}", "trait-agent", None)], (
        "the composer keeps the trait's copy (first wins) and drops the role-level remove"
    )
    assert plan.diagnostics == ()


def test_a_namespaced_skill_names_its_payload_by_the_same_path_on_every_channel(tmp_path: Path):
    from ai_hats.resolver import LibraryResolver
    from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent

    skill = tmp_path / "skills" / "dev" / "py"
    (skill / "hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: dev::py\nai_hats:\n  git_hooks:\n    pre-commit: [hooks/g.sh]\n---\n# x\n"
    )
    (skill / "hooks" / "g.sh").write_text("#!/bin/sh\n")
    (skill / "hooks" / "g.sh").chmod(0o755)
    (tmp_path / "roles" / "r").mkdir(parents=True)
    (tmp_path / "roles" / "r" / "config.yaml").write_text(
        "name: r\ncomposition:\n  skills: [dev::py]\n"
    )
    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[ResolvedComponent("dev::py", ComponentKind.SKILL, skill)],
        injections=[],
        checks=(
            ResolvedCheck(
                app="wt",
                path=(),
                run="dev::py/hooks/g.sh",
                at=("pre-merge",),
                cargo={},
                on_error="warn",
                script_path=skill / "hooks" / "g.sh",
                declared_by="r",
            ),
        ),
    )
    plan = adapt(result, identity="r", resolver=LibraryResolver([tmp_path]), overlays=())
    assert plan.hooks.git[0].payload == plan.hooks.workflow[0].payload
    assert plan.hooks.git[0].payload.path == (skill / "hooks" / "g.sh").resolve()
    assert [s.name for s in plan.skills] == ["skills::dev::py"]


def test_every_path_in_the_plan_is_absolute(maintainer):
    _asm, _result, plan = maintainer
    found: list[str] = []

    def walk(value, where):
        if isinstance(value, Path):
            if not value.is_absolute():
                found.append(f"{where} = {value}")
        elif dataclasses.is_dataclass(value):
            for f in dataclasses.fields(value):
                walk(getattr(value, f.name), f"{where}.{f.name}")
        elif isinstance(value, (tuple, list)):
            for i, item in enumerate(value):
                walk(item, f"{where}[{i}]")

    walk(plan, "plan")
    assert found == []
    with pytest.raises(ValueError):
        dataclasses.replace(plan.skills[0], path=Path("skills/relative"))
