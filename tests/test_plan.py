"""The composition half of the materialization plan, adapted from today's path.

Real library, real ``maintainer``: the names are the flat model's, the text is
what ``show-prompt`` prints today, every absence is ``None``, and two adapts of
one composition are ``==`` (ADR-0036 D1, D7).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.config.overlay import OverlayConfig
from ai_hats.materialize import compose_to_run
from ai_hats.surface_registry import get_surface
from ai_hats.surfaces import HookEvent, adapt
from ai_hats.surfaces.plan import (
    Executable,
    ExternalHook,
    OnError,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    TraceEntry,
    home_of,
)


@pytest.fixture
def maintainer(tmp_path: Path):
    """Function-scoped on purpose: a module-scoped assembler would be built
    before the per-test user-home isolation and read the developer's roots."""
    project = tmp_path / "proj"
    project.mkdir()
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    plan = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    return asm, result, plan


def test_the_rendered_blocks_are_what_the_session_writes_today(maintainer):
    """One producer (ADR-0036 D5): the plan renders its blocks, and on the
    current path that rendering is byte-equal to ``compose_sections`` after
    the expansions every surface performs on its way to the file."""
    asm, result, plan = maintainer
    from ai_hats.placeholders import expand_path_placeholders

    sections = get_surface("claude").build_system_prompt(result)
    assert plan.prompt.text == expand_path_placeholders(sections, asm.layout)
    assert plan.prompt.text != sections, "the composed text carries a placeholder to expand"
    assert len(plan.prompt.text) > 10_000


def test_a_placeholder_in_a_member_is_expanded_for_the_layout(tmp_path: Path):
    from ai_hats.resolver import LibraryResolver
    from ai_hats_core import CompositionResult

    text = "Read <project_dir>/README.md and <ai_hats_dir>/STATE.md"
    result = CompositionResult(
        name="r", priorities=[], rules=[], skills=[], injections=[text], role_injection=text
    )
    plan = adapt(
        result,
        identity="r",
        layout=ProjectLayout.at(tmp_path),
        resolver=LibraryResolver([tmp_path]),
        overlays=(),
        diagnostics=[],
    )
    [member] = plan.prompt.blocks[0].members
    assert member.name == "r::prompt"
    assert "<project_dir>" not in member.text and str(tmp_path) in member.text
    assert "<ai_hats_dir>" not in member.text


def test_prompt_blocks_carry_named_members_with_their_text(maintainer):
    _asm, _result, plan = maintainer
    assert [b.name for b in plan.prompt.blocks] == ["PRIORITIES", None, "RULES"]
    blocks = {b.name: b for b in plan.prompt.blocks}
    [priorities] = blocks["PRIORITIES"].members
    assert priorities == PromptMember(
        "maintainer::priorities", "1. Reliability\n2. Cleanliness\n3. Velocity", None
    )
    prose = blocks[None].members
    assert "trait-agent::prompt" in [m.name for m in prose]
    assert prose[-1].name == "maintainer::prompt", "the role's own text comes last"
    backlog = next(m for m in blocks["RULES"].members if m.name == "rules::rule_backlog_discipline")
    assert backlog.heading == "rule_backlog_discipline"
    assert backlog.text.startswith("# Rule: Backlog Discipline")


def test_a_named_block_may_appear_once_and_nameless_prose_anywhere():
    prose = PromptMember("x::prompt", "x", None)
    rule = PromptMember("rules::r", "R", "r")
    Prompt(
        (PromptBlock(None, (prose,)), PromptBlock("RULES", (rule,)), PromptBlock(None, (prose,)))
    )
    with pytest.raises(ValueError, match="twice"):
        Prompt(
            (
                PromptBlock("RULES", (rule,)),
                PromptBlock(None, (prose,)),
                PromptBlock("RULES", (rule,)),
            )
        )
    with pytest.raises(ValueError, match="no members"):
        PromptBlock("RULES", ())


def test_the_rendering_rule_matches_todays_sections():
    prompt = Prompt(
        (
            PromptBlock("PRIORITIES", (PromptMember("r::priorities", "1. a\n2. b", None),)),
            PromptBlock(
                None, (PromptMember("t::prompt", "T", None), PromptMember("r::prompt", "R", None))
            ),
            PromptBlock(
                "RULES", (PromptMember("rules::x", "X\n", "x"), PromptMember("rules::y", "Y", "y"))
            ),
        )
    )
    assert (
        prompt.text == "## PRIORITIES\n1. a\n2. b\n\nT\n\nR\n\n## RULES\n\n### x\nX\n\n\n### y\nY\n"
    )


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


def test_a_skill_carries_its_document_as_the_agent_reads_it(maintainer):
    """SKILL.md expanded for the layout: every surface renders it the same way
    on its way to the mirror, so it is a fact of the composition, and the
    planner never opens the tree to produce it."""
    from ai_hats.placeholders import expand_fsm_edges_token, expand_path_placeholders

    asm, _result, plan = maintainer
    sample = next(
        s for s in plan.skills if "{{backlog_fsm_edges}}" in (s.path / "SKILL.md").read_text()
    )
    source = (sample.path / "SKILL.md").read_text()
    assert sample.document == expand_fsm_edges_token(
        expand_path_placeholders(source, asm.layout), asm.layout
    )
    assert "{{backlog_fsm_edges}}" not in sample.document
    assert all(s.document is not None for s in plan.skills), "every composed skill ships one"


def test_a_skill_names_the_directories_the_agent_calls_by_name(maintainer):
    """``scripts`` and ``bin`` go on the child's PATH; which of them a tree has
    is a fact of the library, read once by the adapter and never by a planner."""
    _asm, _result, plan = maintainer
    for skill in plan.skills:
        assert skill.on_path == tuple(d for d in ("scripts", "bin") if (skill.path / d).is_dir())
    assert any(skill.on_path for skill in plan.skills), "the sample must ship at least one"


def test_two_skills_shipping_one_script_name_is_a_diagnostic(tmp_path: Path):
    """The later skill's script is shadowed on PATH; the adapter says so where
    it reads the trees, and the plan carries both skills unchanged."""
    from ai_hats.diagnostics import Level
    from ai_hats.resolver import LibraryResolver
    from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

    skills = []
    for name in ("first", "second"):
        skill = tmp_path / "skills" / name
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n")
        (skill / "scripts" / "same.sh").write_text("#!/bin/sh\n")
        skills.append(ResolvedComponent(name, ComponentKind.SKILL, skill))
    (tmp_path / "roles" / "r").mkdir(parents=True)
    (tmp_path / "roles" / "r" / "config.yaml").write_text(
        "name: r\ncomposition:\n  skills: [first, second]\n"
    )
    result = CompositionResult(name="r", priorities=[], rules=[], skills=skills, injections=[])
    found = []
    plan = adapt(
        result,
        identity="r",
        layout=ProjectLayout.at(tmp_path),
        resolver=LibraryResolver([tmp_path]),
        overlays=(),
        diagnostics=found,
    )
    assert [s.on_path for s in plan.skills] == [("scripts",), ("scripts",)]
    assert [d.level for d in found] == [Level.WARN]
    assert "same.sh" in found[0].text and "second" in found[0].text


def test_runtime_hooks_are_the_rows_a_surface_wires(maintainer):
    _asm, _result, plan = maintainer
    guard = [h for h in plan.hooks.runtime if h.run.path.name == "safety_gate.py"]
    assert [(h.at, h.matcher) for h in guard] == [
        (HookEvent.PRE_TOOL_USE, "Bash|run_command|execute")
    ]
    assert guard[0].run.path.is_absolute() and len(guard[0].run.content_digest) == 64
    skill, inside = home_of(guard[0].run, plan.skills)
    assert skill.name == "skills::safety-guard" and str(inside) == "hooks/safety_gate.py"


def test_a_runtime_hook_binds_to_an_event_of_the_channel_not_to_a_spelling():
    """The channel's event set is closed (ADR-0020); a row written in a
    surface's own spelling would go quiet on every other surface."""
    run = Executable(Path("/lib/skills/g/hooks/g.py"), "ab" * 32)
    RuntimeHook(HookEvent.POST_TOOL_USE, "Edit", run)
    with pytest.raises(ValueError, match="HookEvent"):
        RuntimeHook("PreToolUse", "Edit", run)  # type: ignore[arg-type]


def test_external_hooks_carry_git_worktree_checks_and_consent_one_row_per_point(maintainer):
    _asm, result, plan = maintainer
    gate = next(s.source_path for s in result.skills if s.name == "quality-gate")
    rows = {(h.app, h.object, h.at): h for h in plan.hooks.external}
    done = rows[("rack", "tasks", "->done")]
    assert done.run.path == gate / "hooks" / "done-gate.sh"
    assert (done.on_error, done.declared_by) == (OnError.REFUSE, "ai-hats-gates")
    merge = rows[("wt", None, "pre-merge")]
    assert merge.run.path == gate / "hooks" / "merge-gate.sh" and merge.on_error is OnError.REFUSE
    push = rows[("git", None, "pre-push")]
    assert push.run.path.name == "pre-push-e2e-master.sh"
    assert (push.on_error, push.declared_by) == (None, "skills::quality-gate")
    create = rows[("wt", None, "create")]
    assert (
        create.run.path.name == "provision-venv.sh"
        and create.declared_by == "skills::worktree-venv"
    )
    consent = rows[("consent_gate", "rack.transition", "->done")]
    assert consent == ExternalHook(
        "consent_gate", "rack.transition", "->done", None, None, "trait-agent"
    )
    assert ("consent_gate", "wt.merge", "pre-merge") in rows
    assert not any(h.app == "wt" and h.at.startswith("teardown") for h in plan.hooks.external), (
        "the shipped library declares no wt_out hook"
    )


def test_a_wt_out_hook_without_on_fires_on_every_teardown_event(tmp_path: Path):
    from ai_hats.resolver import LibraryResolver
    from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

    skill = tmp_path / "skills" / "drain"
    (skill / "hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drain\nai_hats:\n  worktree:\n    wt_out:\n      - script: hooks/d.sh\n---\n# d\n"
    )
    (skill / "hooks" / "d.sh").write_text("#!/bin/sh\n")
    (skill / "hooks" / "d.sh").chmod(0o755)
    (tmp_path / "roles" / "r").mkdir(parents=True)
    (tmp_path / "roles" / "r" / "config.yaml").write_text(
        "name: r\ncomposition:\n  skills: [drain]\n"
    )
    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[ResolvedComponent("drain", ComponentKind.SKILL, skill)],
        injections=[],
    )
    plan = adapt(
        result,
        identity="r",
        layout=ProjectLayout.at(tmp_path),
        resolver=LibraryResolver([tmp_path]),
        overlays=(),
        diagnostics=[],
    )
    assert [(h.app, h.at) for h in plan.hooks.external] == [
        ("wt", "teardown[merge]"),
        ("wt", "teardown[discard]"),
        ("wt", "teardown[cleanup]"),
    ]
    assert len({h.run for h in plan.hooks.external}) == 1


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
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=[(overlay, "project")],
        diagnostics=[],
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
    again = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    assert again == plan and again.digest == plan.digest
    extra = dataclasses.replace(result, skills=[*result.skills, result.skills[0]])
    changed = adapt(
        extra,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    assert changed != plan and changed.digest != plan.digest


def test_the_plan_digest_folds_every_record_down_the_tree(maintainer):
    _asm, _result, plan = maintainer
    executable = plan.hooks.runtime[0].run
    other = dataclasses.replace(executable, content_digest="0" * 64)
    hooks = dataclasses.replace(
        plan.hooks,
        runtime=(dataclasses.replace(plan.hooks.runtime[0], run=other), *plan.hooks.runtime[1:]),
    )
    assert other.digest != executable.digest
    assert hooks.digest != plan.hooks.digest
    assert dataclasses.replace(plan, hooks=hooks).digest != plan.digest


def test_the_funnel_walk_finds_an_empty_string_posing_as_absence():
    findings: list[str] = []
    _walk(ExternalHook("consent_gate", "", "->done", None, None, "trait-agent"), "c", findings)
    assert findings == ["c.object = '' where absence must be None"]


def test_a_declared_script_that_is_missing_is_a_diagnostic_and_no_hook(tmp_path: Path):
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
    found = []
    plan = adapt(
        result,
        identity="r",
        layout=ProjectLayout.at(tmp_path),
        resolver=LibraryResolver([tmp_path]),
        overlays=(),
        diagnostics=found,
    )
    assert plan.hooks.runtime == ()
    assert [d.level for d in found] == [Level.WARN]
    assert "skills::guard" in found[0].text and "hooks/gone.sh" in found[0].text


def test_trace_reads_a_same_layer_remove_and_add_as_the_composer_does(maintainer):
    """``remove: [X]`` + ``add: [X]`` in one layer is the move-to-end reorder,
    and X stays composed — the trace must hold one row for it, not two."""
    asm, _result, _plan = maintainer
    rule = "rule_backlog_discipline"
    overlay = OverlayConfig(add_rules=[rule], remove_rules=[rule])
    result = compose_to_run(asm, "maintainer", runtime_overlay=overlay)
    found: list = []
    plan = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=[(overlay, "project")],
        diagnostics=found,
    )
    rows = [t for t in plan.trace if t.term == f"rules::{rule}"]
    assert rows == [TraceEntry(f"rules::{rule}", "trait-agent", None)], (
        "the composer keeps the trait's copy (first wins) and drops the role-level remove"
    )
    assert found == []


def test_a_namespaced_skill_names_its_executable_by_the_same_path_on_every_channel(tmp_path: Path):
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
    plan = adapt(
        result,
        identity="r",
        layout=ProjectLayout.at(tmp_path),
        resolver=LibraryResolver([tmp_path]),
        overlays=(),
        diagnostics=[],
    )
    git, check = plan.hooks.external
    assert git.run == check.run
    assert git.run.path == (skill / "hooks" / "g.sh").resolve()
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
