"""One session on the plan: what planning is told about the machine, and the
pair a launch is (ADR-0036 D2, D4)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import plan_session, probe_host
from ai_hats.surface_registry import get_surface
from ai_hats.surfaces import apply
from ai_hats.surfaces.plan import (
    Host,
    Launched,
    LaunchFlags,
    Prompt,
    PromptBlock,
    PromptMember,
)


def test_the_host_is_probed_once_for_every_command_the_gate_can_wrap(tmp_path: Path):
    """Planning never calls ``which``: the stage before it resolves every
    wrappable command and hands the result in as a value."""
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("rack", "ai-hats"):
        (bins / name).write_text("#!/bin/sh\n")
        (bins / name).chmod(0o700)
    wrapper_bin = tmp_path / "sessions" / "s1" / "consent-wrapper" / "bin"
    wrapper_bin.mkdir(parents=True)
    (wrapper_bin / "rack").write_text("#!/bin/sh\n")
    (wrapper_bin / "rack").chmod(0o700)
    environ = {"PATH": os.pathsep.join([str(wrapper_bin), str(bins)])}

    host = probe_host(environ, python="/opt/py/bin/python3")

    assert host == Host(
        python=Path("/opt/py/bin/python3"),
        path=str(bins),
        commands={"ai-hats": (bins / "ai-hats").resolve(), "rack": (bins / "rack").resolve()},
    )
    assert host.digest == probe_host(environ, python="/opt/py/bin/python3").digest


def test_a_command_missing_from_the_path_is_absent_not_empty(tmp_path: Path):
    host = probe_host({"PATH": str(tmp_path)}, python="/opt/py/bin/python3")
    assert host.commands == {}


def test_a_launch_is_an_argv_or_an_option_document_never_both():
    with pytest.raises(ValueError):
        Launched(args=("claude",), sdk_options={"model": "x"}, env={}, prompt="")
    with pytest.raises(ValueError):
        Launched(args=None, sdk_options=None, env={}, prompt="")
    launched = Launched(args=("claude",), sdk_options=None, env={"A": "1"}, prompt="hi")
    assert launched.args == ("claude",) and launched.sdk_options is None


def test_launch_flags_default_to_what_a_hitl_launch_needs():
    flags = LaunchFlags(
        session_id="s",
        session_dir=Path("/runs/s"),
        trace_path="t",
        root_pid="1",
        provider_session_id="u",
    )
    assert flags.extra_args == () and flags.work_dir is None and flags.brief is None
    assert flags.model is None and flags.claim is True


# ── claude on the real maintainer: pure planning, idempotent application ─────


@pytest.fixture
def maintainer(tmp_path: Path):
    """Function-scoped: a module-scoped assembler would be built before the
    per-test user-home isolation and read the developer's roots."""
    from ai_hats.assembler import Assembler
    from ai_hats.materialize import compose_to_run
    from ai_hats.surfaces import adapt

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


def _claude(asm, composition, root: Path, run_mode: RunMode):
    return get_surface("claude").plan(
        composition,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(),
    )


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_it_is_applied(
    maintainer, tmp_path: Path, run_mode: RunMode
):
    """Planning reads nothing the application wrote (ADR-0036 D2)."""
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    before = _claude(asm, composition, root, run_mode)
    apply(before)
    after = _claude(asm, composition, root, run_mode)
    assert before == after and before.digest == after.digest
    assert len(before.entries) > 40, "the sample composes dozens of skills"
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _claude(asm, more, root, run_mode) != before, "a changed input must show"


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_applying_the_plan_twice_reaches_no_primitive(
    maintainer, tmp_path: Path, writes: list[str], run_mode: RunMode
):
    asm, composition = maintainer
    root = tmp_path / "sessions" / "s1"
    plan = _claude(asm, composition, root, run_mode)
    apply(plan)
    writes.clear()
    assert apply(plan).changed is False
    assert writes == []

    hook = next(
        e for e in plan.entries if e.kind is WriteKind.COPY_TREE and e.target.name == "safety-guard"
    )
    gate = hook.target / "hooks" / "safety_gate.py"
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
    other = _claude(asm, reworded, root, run_mode)
    writes.clear()
    apply(other)
    assert writes == ["write_bytes"], "one changed byte sequence, one write"


# ── the launch pair against today's assemblers: one builder, proven before the cut ──


def _flags(root: Path, **overrides) -> LaunchFlags:
    given = dict(
        session_id="s", session_dir=root, trace_path="t", root_pid="1", provider_session_id="u"
    )
    given.update(overrides)
    return LaunchFlags(**given)


def test_a_hitl_launch_is_todays_argv_and_environment(maintainer, tmp_path: Path):
    from ai_hats.session_artifacts import assemble_launch_command, assemble_launch_env
    from ai_hats.session_plan import launch, launch_env

    asm, composition = maintainer
    root = asm.layout.cache.session("s")
    surface = get_surface("claude")
    plan = plan_session(
        composition,
        surface,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(),
    )
    flags = _flags(root, extra_args=("--model", "opus"))

    launched = launch(plan, flags, layout=asm.layout)

    assert list(launched.args) == assemble_launch_command(
        surface,
        extra_args=["--model", "opus"],
        session_args=list(plan.launch.args),
        provider_session_id="u",
    )
    assert launch_env(plan, surface, flags, layout=asm.layout) == assemble_launch_env(
        surface,
        asm.layout,
        root,
        session_id="s",
        trace_path="t",
        role="maintainer",
        root_pid="1",
        extra_env=dict(plan.env),
        run_mode=RunMode.HITL,
    )
    assert launched.prompt.startswith("<!-- AI-HATS:START -->\n"), "the context file's bytes"
    assert "--session-id" in launched.args and "u" in launched.args


def test_a_cli_sub_agent_launch_is_one_meta_prompt_token(tmp_path: Path):
    """The base ``automate_launch`` builds the prompt from the context entry the
    plan names, the working directory and the brief, and hands it to the CLI
    as the one token ``get_run_command`` appends."""
    from ai_hats.materialization import describe_write_text
    from ai_hats.session_artifacts import assemble_brief, working_directory_section
    from ai_hats.surfaces.cline.provider import ClineSurface
    from ai_hats.surfaces.plan import CompositionPlan, Hooks, Launch, MaterializationPlan
    from ai_hats_core.layout import ProjectLayout

    layout = ProjectLayout.at(tmp_path / "proj")
    root = tmp_path / "sessions" / "s"
    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )
    plan = MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="cline",
        run_mode=RunMode.AUTOMATE,
        policy=SessionPolicy(),
        root=root,
        entries=(describe_write_text(root / "rules.md", "CONTEXT\n"),),
        env={},
        launch=Launch(args=("--config", str(root)), sdk_options=None),
        context=root / "rules.md",
    )
    brief = assemble_brief(layout, task="demo", ticket_id="")
    surface = ClineSurface()

    launched = surface.automate_launch(
        plan, _flags(root, model="m", brief=brief), {}, layout=layout
    )

    prompt = "\n\n".join(["CONTEXT\n", working_directory_section(layout), brief])
    assert launched.prompt == prompt
    assert list(launched.args) == [
        "cline",
        "--config",
        str(root),
        "--model",
        "m",
        "--yolo",
        "--json",
        prompt,
    ]
    other = surface.automate_launch(
        plan, _flags(root, model="m", brief="# TASK\nother"), {}, layout=layout
    )
    assert other.args != launched.args, "a different brief is a different launch"


def test_a_consent_row_of_the_plan_carries_the_ends_the_guard_keys_on():
    """The guard is stdlib-only and reads fields, never grammar: the row names
    the operation as its path and the selector's two ends already parsed."""
    from ai_hats.session_report import consent_row
    from ai_hats.surfaces.plan import ExternalHook

    hook = ExternalHook(
        "consent_gate", "rack.transition", "plan->execute", None, None, "trait-agent"
    )
    assert consent_row(hook) == {
        "app": "consent_gate",
        "path": ["rack.transition"],
        "selector": "plan->execute",
        "from": "plan",
        "to": "execute",
        "declared_by": "trait-agent",
    }


def test_the_record_names_what_application_did_only_when_it_did(maintainer, tmp_path: Path):
    from ai_hats.session_plan import launch, session_record

    asm, composition = maintainer
    root = tmp_path / "sessions" / "s"
    plan = _claude(asm, composition, root, RunMode.HITL)
    launched = launch(plan, _flags(root), layout=asm.layout)

    planned = session_record(plan, launched, role="maintainer", cwd="x")
    assert "outcome" not in planned["materialized"][0]
    assert set(planned) == {
        "role",
        "provider",
        "run_mode",
        "cwd",
        "policy",
        "launch",
        "env_keys",
        "prompt",
        "materialized",
        "checks",
        "consent",
        "notes",
        "composition",
    }
    applied = session_record(plan, launched, apply(plan), role="maintainer", cwd="x")
    tree = next(e for e in applied["materialized"] if e["kind"] == "copy_tree")
    assert tree["outcome"] == "written" and tree["files"] > 0
    assert [e["outcome"] for e in applied["materialized"] if e["kind"] == "write_text"][
        0
    ] == "written"
    assert applied["prompt"] == str(root / "prompt.md")
    assert applied["consent"], "the maintainer role declares consent"


# ── the context the plan names, the home the surface probes, the checks it covers ──


def _bare_plan(root: Path, entries=(), *, context: Path | None = None, surface: str = "cline"):
    from ai_hats.surfaces.plan import CompositionPlan, Hooks, Launch, MaterializationPlan

    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface=surface,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=tuple(entries),
        env={},
        launch=Launch(args=(), sdk_options=None),
        context=context,
    )


def test_the_launch_reports_the_context_entry_the_plan_names_else_the_prompt(tmp_path: Path):
    """No ``.md`` heuristic: codex and opencode write ``SKILL.md`` documents
    under the root, so the plan says which entry, if any, is the context."""
    from ai_hats.materialization import describe_write_text
    from ai_hats.session_plan import launch, session_record
    from ai_hats.surfaces import context_entry, context_text
    from ai_hats_core.layout import ProjectLayout

    layout = ProjectLayout.at(tmp_path / "proj")
    root = tmp_path / "sessions" / "s"
    document = describe_write_text(root / "skills" / "s" / "SKILL.md", "# a skill\n")
    context = describe_write_text(root / "rules.md", "CONTEXT\n")

    named = _bare_plan(root, (document, context), context=root / "rules.md")
    assert context_entry(named) is context
    launched = launch(named, _flags(root, claim=False), layout=layout)
    assert launched.prompt == "CONTEXT\n"
    assert session_record(named, launched, role="r")["prompt"] == str(root / "rules.md")

    inline = _bare_plan(root, (document,))
    assert context_entry(inline) is None
    assert context_text(inline) == inline.prompt.text
    launched = launch(inline, _flags(root, claim=False), layout=layout)
    assert launched.prompt == "# r\n", "the surface prompt, never the skill document"
    assert session_record(inline, launched, role="r")["prompt"] is None


def test_a_policy_that_withholds_the_context_hands_the_agent_none_of_it(tmp_path: Path):
    """The prompt half is always planned; whether the agent reads it is the
    policy's — so an inline surface's sub-agent token carries no role text
    under ``context=False``, as the builder's ``full_content`` was ``None``."""
    import dataclasses

    from ai_hats.surfaces import context_text

    root = tmp_path / "sessions" / "s"
    withheld = dataclasses.replace(_bare_plan(root, ()), policy=SessionPolicy(context=False))

    assert withheld.prompt.text == "# r\n", "the plan still holds the prompt"
    assert context_text(withheld) == ""


def test_a_context_no_entry_writes_is_refused_before_anything_is_touched(tmp_path: Path):
    from ai_hats.materialization import describe_write_text
    from ai_hats.surfaces import ContextUnwritten, validate

    root = tmp_path / "sessions" / "s"
    context = describe_write_text(root / "rules.md", "CONTEXT\n")
    validate(_bare_plan(root, (context,), context=root / "rules.md"))
    with pytest.raises(ContextUnwritten):
        validate(_bare_plan(root, (), context=root / "rules.md"))
    with pytest.raises(ContextUnwritten):
        validate(_bare_plan(root, (context,), context=root / "other.md"))


def test_the_host_carries_the_home_the_surface_probes(tmp_path: Path):
    """The one read of the person's home is the surface's, taken by
    ``probe_host`` for the surface in hand; a surface without one leaves ``None``."""
    from ai_hats.surfaces.cline.provider import ClineSurface
    from ai_hats.surfaces.plan import Digested

    @dataclasses.dataclass(frozen=True)
    class Home(Digested):
        root: Path
        entries: tuple[str, ...]

    class Homed(ClineSurface):
        def probe_home(self, environ):
            return Home(Path(environ["HOME"]) / ".tool", ("config.json",))

    environ = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    bare = probe_host(environ, python="/opt/py", surface=ClineSurface())
    homed = probe_host(environ, python="/opt/py", surface=Homed())
    assert bare.home is None and probe_host(environ, python="/opt/py").home is None
    assert homed.home == Home(tmp_path / ".tool", ("config.json",))
    assert homed.digest != bare.digest, "the home is an input of planning"


def test_the_record_says_where_each_check_runs_from_off_the_plans_own_entries(tmp_path: Path):
    """``runs_from`` is the mirror the plan writes for the check's skill; a
    script outside every composed skill has no mirror and says so."""
    from ai_hats.materialization import MaterializationEntry
    from ai_hats.surfaces import checks_record
    from ai_hats.surfaces.plan import (
        CompositionPlan,
        Executable,
        ExternalHook,
        Hooks,
        Launch,
        MaterializationPlan,
        OnError,
        Skill,
    )

    root = tmp_path / "sessions" / "s"
    skill_dir = tmp_path / "lib" / "skills" / "s"
    skill = Skill(name="skills::s", path=skill_dir, content_digest="t")
    inside = Executable(skill_dir / "hooks" / "gate.sh", "g")
    outside = Executable(tmp_path / "elsewhere" / "gate.sh", "e")

    def check(run: Executable) -> ExternalHook:
        return ExternalHook("rack", "tasks", "plan->execute", run, OnError.REFUSE, "r")

    consent = ExternalHook("consent_gate", "rack.transition", "->done", None, None, "r")
    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(skill,),
        hooks=Hooks((), (check(inside), check(outside), consent)),
        trace=(),
    )
    mirror = MaterializationEntry(
        kind=WriteKind.COPY_TREE, target=root / "skills" / "s", source=skill_dir, tree_digest="t"
    )

    def plan(entries) -> MaterializationPlan:
        return MaterializationPlan(
            composition=composition,
            prompt=composition.prompt,
            surface="cline",
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=root,
            entries=entries,
            env={},
            launch=Launch(args=(), sdk_options=None),
        )

    mirrored, unmirrored = checks_record(plan((mirror,)))
    assert mirrored == {
        "skill": "s",
        "script": "hooks/gate.sh",
        "app": "rack",
        "object": "tasks",
        "at": "plan->execute",
        "on_error": "refuse",
        "declared_by": "r",
        "runs_from": str(root / "skills" / "s" / "hooks" / "gate.sh"),
        "planned": True,
    }
    assert unmirrored["skill"] is None and unmirrored["script"] == str(outside.path)
    assert unmirrored["runs_from"] is None and unmirrored["planned"] is False
    (unplanned, _) = checks_record(plan(()))
    assert unplanned["runs_from"] is None and unplanned["planned"] is False
    assert unplanned["skill"] == "s", "the skill is composed; this launch just writes no mirror"
