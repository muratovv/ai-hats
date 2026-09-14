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
from ai_hats.plan import (
    Consent,
    OnError,
    TraceEntry,
    WorkflowHook,
    WorktreeHook,
    adapt,
)
from ai_hats.surface_registry import get_surface


@pytest.fixture(scope="module")
def maintainer(tmp_path_factory):
    project = tmp_path_factory.mktemp("proj")
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    plan, sources = adapt(result, identity="maintainer", resolver=asm.resolver)
    return asm, result, plan, sources


def test_prompt_text_is_what_show_prompt_prints(maintainer):
    _asm, result, plan, _sources = maintainer
    assert plan.prompt.text == get_surface("claude").build_system_prompt(result)


def test_prompt_members_name_the_sections_in_text_order(maintainer):
    _asm, _result, plan, _sources = maintainer
    pairs = [(m.block, m.name) for m in plan.prompt.members]
    assert pairs[0] == ("PRIORITIES", "maintainer::priorities")
    assert ("body", "trait-agent::prompt") in pairs
    assert ("RULES", "rules::rule_backlog_discipline") in pairs
    body = [name for block, name in pairs if block == "body"]
    assert body[-1] == "maintainer::prompt", "the role's own text is the last body member"
    rules = [name for block, name in pairs if block == "RULES"]
    assert pairs.index(("body", body[-1])) < pairs.index(("RULES", rules[0]))
    for name in rules:
        assert f"### {name.removeprefix('rules::')}\n" in plan.prompt.text


def test_skills_carry_full_names_in_composition_order(maintainer):
    _asm, result, plan, _sources = maintainer
    assert plan.skills == tuple(f"skills::{s.name}" for s in result.skills)
    assert "skills::hatrack" in plan.skills


def test_hooks_by_kind_with_payload_paths_from_the_layer_root(maintainer):
    _asm, _result, plan, _sources = maintainer
    assert plan.hooks.workflow == (
        WorkflowHook(
            "rack", "tasks", "->review", "skills/quality-gate/hooks/review-gate.sh", OnError.REFUSE
        ),
        WorkflowHook(
            "rack", "tasks", "->done", "skills/quality-gate/hooks/done-gate.sh", OnError.REFUSE
        ),
        WorkflowHook(
            "wt", None, "pre-merge", "skills/quality-gate/hooks/merge-gate.sh", OnError.REFUSE
        ),
    )
    assert (
        WorktreeHook("wt_in", None, "skills/worktree-venv/hooks/provision-venv.sh")
        in plan.hooks.worktree
    )
    gate = [h for h in plan.hooks.runtime if h.run == "skills/safety-guard/hooks/safety_gate.py"]
    assert [h.at for h in gate] == ["PreToolUse"]
    assert ("pre-push", "skills/quality-gate/git_hooks/pre-push-e2e-master.sh") in [
        (h.at, h.run) for h in plan.hooks.git
    ]


def test_consent_carries_the_parsed_ends(maintainer):
    _asm, _result, plan, _sources = maintainer
    assert (
        Consent("rack.transition", "plan->execute", "plan", "execute", "trait-agent", None)
        in plan.consent
    )
    assert Consent("rack.transition", "->done", None, "done", "trait-agent", None) in plan.consent
    assert Consent("wt.merge", "pre-merge", None, None, "trait-agent", None) in plan.consent


def test_trace_attributes_each_term_to_its_bringer(maintainer):
    _asm, _result, plan, _sources = maintainer
    assert TraceEntry("trait-agent", "maintainer", None) in plan.trace
    assert TraceEntry("rules::rule_backlog_discipline", "trait-agent", None) in plan.trace
    assert TraceEntry("skills::quality-gate", "ai-hats-dev", None) in plan.trace
    assert TraceEntry("ai-hats-gates", "maintainer", None) in plan.trace


def test_trace_names_the_override_that_added_or_removed_a_term(maintainer):
    asm, _result, _plan, _sources = maintainer
    overlay = OverlayConfig(
        add_traits=["worker"], remove_traits=["ai-hats-gates"], remove_skills=["hatrack"]
    )
    result = compose_to_run(asm, "maintainer", runtime_overlay=overlay)
    plan, _ = adapt(
        result, identity="maintainer", resolver=asm.resolver, overlays=[(overlay, "project")]
    )
    assert TraceEntry("worker", "overrides::project", None) in plan.trace
    assert TraceEntry("ai-hats-gates", "maintainer", "overrides::project") in plan.trace
    assert TraceEntry("skills::hatrack", "trait-agent", "overrides::project") in plan.trace
    assert "skills::hatrack" not in plan.skills


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
    _asm, _result, plan, _sources = maintainer
    findings: list[str] = []
    _walk(plan, "plan", findings)
    assert findings == []


def test_two_adapts_of_one_composition_are_equal_and_a_changed_input_is_not(maintainer):
    asm, result, plan, _sources = maintainer
    again, _ = adapt(result, identity="maintainer", resolver=asm.resolver)
    assert again == plan
    extra = dataclasses.replace(result, skills=[*result.skills, result.skills[0]])
    changed, _ = adapt(extra, identity="maintainer", resolver=asm.resolver)
    assert changed != plan


def test_sources_locate_every_skill_outside_the_comparable_half(maintainer):
    _asm, result, plan, sources = maintainer
    assert set(sources.skills) == set(plan.skills)
    hatrack = sources.skills["skills::hatrack"]
    assert hatrack.path == next(s.source_path for s in result.skills if s.name == "hatrack")
    assert len(hatrack.digest) == 64
    assert not any(isinstance(v, Path) for v in vars(plan).values())


def test_the_funnel_walk_finds_an_empty_string_posing_as_absence():
    findings: list[str] = []
    _walk(Consent("rack.transition", "->done", "", "done", "trait-agent", None), "c", findings)
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
    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[ResolvedComponent("guard", ComponentKind.SKILL, skill)],
        injections=[],
    )
    plan, _ = adapt(result, identity="r", resolver=LibraryResolver([tmp_path]))
    assert plan.hooks.runtime == ()
    assert [d.level for d in plan.diagnostics] == [Level.WARN]
    assert "skills::guard" in plan.diagnostics[0].message
    assert "hooks/gone.sh" in plan.diagnostics[0].message
