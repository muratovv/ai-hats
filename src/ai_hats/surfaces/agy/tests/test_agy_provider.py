"""AgySurface: the session it plans, and the launch it shapes."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.materialization import WriteKind
from ai_hats.materialize import compose_to_run
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, gemini_md
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import adapt
from ai_hats.surfaces.agy.provider import AgySurface, agy_user_settings_json
from ai_hats.surfaces.plan import (
    CompositionPlan,
    EscapeUndeclared,
    Executable,
    Hooks,
    LaunchFlags,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
    apply,
    context_text,
    validate,
)
from ai_hats.surfaces.hook_channel import HookEvent


def _library(root: Path, skill_md: str, script: tuple[str, str] | None = None) -> Path:
    """A library with one skill and one role composing it."""
    lib = root / "lib"
    skill_dir = lib / "skills" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md)
    if script is not None:
        name, body = script
        (skill_dir / name).write_text(body)
        (skill_dir / name).chmod(0o755)
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Quality\n"
        "composition:\n  skills: [s]\n"
        "injection: Role body.\n"
    )
    return lib


def _compose(
    project: Path, lib: Path, diagnostics: list | None = None
) -> tuple[Assembler, CompositionPlan]:
    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = compose_to_run(asm, "test-role")
    composition = adapt(
        result,
        identity="test-role",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[] if diagnostics is None else diagnostics,
    )
    return asm, composition


@pytest.fixture
def agy_project(tmp_path, monkeypatch):
    """Minimal library + role composed for the agy surface, the person's home pinned."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(tmp_path, "---\nname: s\ndescription: x\n---\n# body\n")
    return _compose(project, lib)


def _plan(asm, composition, root: Path, run_mode: RunMode, policy: SessionPolicy | None = None):
    surface = AgySurface()
    return surface.plan(
        composition,
        run_mode=run_mode,
        policy=policy or SessionPolicy(),
        root=root,
        layout=asm.layout,
        host=probe_host(surface=surface),
    )


def _flags(root: Path, **overrides) -> LaunchFlags:
    given = dict(
        session_id="s", session_dir=root, trace_path="t", root_pid="1", provider_session_id="u"
    )
    given.update(overrides)
    return LaunchFlags(**given)


# --- the plan --------------------------------------------------------------


def test_a_hitl_plan_writes_the_rules_dir_and_hands_it_over_with_add_dir(agy_project, tmp_path):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"
    host = probe_host(surface=AgySurface())

    plan = _plan(asm, composition, root, RunMode.HITL)

    assert plan.launch.args == ("--add-dir", str(root / "rules"))
    assert plan.context == root / "rules" / "GEMINI.md"
    assert context_text(plan) == plan.prompt.text
    assert "## PRIORITIES" in plan.prompt.text and "Role body." in plan.prompt.text
    assert "## AVAILABLE SKILLS" not in plan.prompt.text, "agy discovers skills natively"
    assert plan.env["AI_HATS_SESSION_CACHE_DIR"] == str(root)
    assert plan.env["AI_HATS_PYTHON"] == str(host.python)
    assert plan.env["AI_HATS_PROJECT_DIR"] == str(asm.layout.root)
    apply(plan)
    assert (root / "rules" / "GEMINI.md").read_text() == plan.prompt.text


def test_a_sub_agent_plan_writes_no_context_and_hands_it_in_the_prompt_token(agy_project, tmp_path):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.AUTOMATE)

    assert plan.context is None and plan.launch.args == ()
    assert not any(e.target.name == "GEMINI.md" for e in plan.entries)
    launched = AgySurface().automate_launch(
        plan, _flags(root, brief="# TASK\ndo it"), {}, layout=asm.layout
    )
    assert launched.args[-2:-1] == ("-p",) and launched.args[-1].startswith(plan.prompt.text)
    assert launched.args[-1].endswith("# TASK\ndo it")


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_skills_mirror_under_the_rules_dir_in_both_modes(agy_project, tmp_path, run_mode):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, run_mode)
    apply(plan)

    assert (root / "rules" / ".agents" / "skills" / "s" / "SKILL.md").is_file()


def test_two_roots_are_two_rules_dirs(agy_project, tmp_path):
    asm, composition = agy_project

    a = _plan(asm, composition, tmp_path / "sessions" / "a", RunMode.HITL)
    b = _plan(asm, composition, tmp_path / "sessions" / "b", RunMode.HITL)

    assert a.launch.args[1] != b.launch.args[1]


def test_the_dispatcher_is_registered_in_the_persons_settings_outside_the_root(
    agy_project, tmp_path
):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL)

    [entry] = [e for e in plan.entries if e.kind is WriteKind.MERGE_JSON]
    assert entry.target == agy_user_settings_json() and entry.escape
    assert entry.target.is_relative_to(tmp_path / "home")
    validate(plan)
    stripped = dataclasses.replace(
        plan,
        entries=tuple(
            dataclasses.replace(e, escape=False) if e is entry else e for e in plan.entries
        ),
    )
    with pytest.raises(EscapeUndeclared):
        validate(stripped)


def test_hooks_off_registers_no_dispatcher_and_context_off_writes_no_rules(agy_project, tmp_path):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL, SessionPolicy(context=False, hooks=False))

    assert [e.kind for e in plan.entries] == [
        WriteKind.MKDIR,
        WriteKind.MKDIR,
        WriteKind.COPY_TREE,
        WriteKind.WRITE_TEXT,
    ]
    assert plan.context is None and plan.launch.args == ()
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_application(
    agy_project, tmp_path, run_mode
):
    asm, composition = agy_project
    root = tmp_path / "sessions" / "s1"

    before = _plan(asm, composition, root, run_mode)
    apply(before)
    after = _plan(asm, composition, root, run_mode)

    assert before == after and before.digest == after.digest
    assert apply(after).changed is False
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _plan(asm, more, root, run_mode) != before, "a changed input must show"


# --- hooks -------------------------------------------------------------------


_HOOKED_SKILL_MD = (
    "---\n"
    "name: s\n"
    "description: skill with hook\n"
    "ai_hats:\n"
    "  runtime_hooks:\n"
    "    PreToolUse:\n"
    "      - matcher: Edit\n"
    "        script: run_hook.sh\n"
    "---\n"
    "# body\n"
)


def test_the_maintainer_mirror_carries_the_worktree_gate(tmp_path: Path, monkeypatch) -> None:
    """The real role's gate script lands in the mirror and the manifest names
    it there; the project root stays clean of any settings file."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)
    project = tmp_path / "project"
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
    root = asm.layout.cache.session("sid-wt")

    plan = _plan(asm, composition, root, RunMode.HITL)
    apply(plan)

    wt_skill_dir = root / "rules" / ".agents" / "skills" / "worktree-isolation"
    assert (wt_skill_dir / "SKILL.md").is_file()
    assert (wt_skill_dir / "hooks" / "wt_gate.py").is_file()
    data = json.loads((root / "hooks.json").read_text())
    assert any(
        str(wt_skill_dir / "hooks" / "wt_gate.py") == h["command"] for h in data["PreToolUse"]
    )
    assert not (project / ".gemini" / "settings.json").exists(), "Clean-Root Invariant"
    assert agy_user_settings_json().is_file(), "the dispatcher lives in the person's settings"


def test_a_sub_agent_plan_arms_the_hooks_the_dispatcher_fires(tmp_path: Path, monkeypatch) -> None:
    """The env the plan carries is the pin the dispatcher reads — the session
    hook fires from the mirror the plan wrote, not from a value the test invented."""
    from ai_hats.session_identity import SessionIdentity
    from ai_hats.surfaces.agy.hook_dispatcher import dispatch_hook

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "hook_fired.txt"
    lib = _library(
        tmp_path, _HOOKED_SKILL_MD, ("run_hook.sh", f"#!/bin/sh\necho 'FIRED' > '{marker}'\n")
    )
    asm, composition = _compose(project, lib)
    root = asm.layout.cache.session("sid-auto")

    plan = _plan(asm, composition, root, RunMode.AUTOMATE)
    apply(plan)

    data = json.loads((root / "hooks.json").read_text())
    assert any("run_hook.sh" in h["command"] for h in data["PreToolUse"])
    assert "## PRIORITIES" in plan.prompt.text and "Role body." in plan.prompt.text
    identity = SessionIdentity(
        id="sid-auto",
        role="test-role",
        provider="agy",
        project_dir=project,
        session_dir=project / "session",
    )
    for key, value in {**plan.env, **identity.to_env()}.items():
        monkeypatch.setenv(key, value)
    assert dispatch_hook("PreToolUse", tool_name="Edit") == 0
    assert marker.read_text().strip() == "FIRED"


def test_a_script_missing_from_the_skill_is_a_diagnostic_and_no_row(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(tmp_path, _HOOKED_SKILL_MD)  # declared, never shipped
    diagnostics: list = []
    asm, composition = _compose(project, lib, diagnostics)
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL)

    [manifest] = [e for e in plan.entries if e.target.name == "hooks.json"]
    assert json.loads(manifest.content or "") == {}, (
        "no command may point at a file that is not there"
    )
    [notice] = [d.render() for d in diagnostics if "run_hook.sh" in d.render()]
    assert "skills::s" in notice and "will not run" in notice


def test_a_hook_outside_every_composed_skill_refuses_the_plan(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "proj")
    elsewhere = tmp_path / "elsewhere" / "guard.sh"
    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(Skill(name="skills::s", path=tmp_path / "lib" / "s", content_digest="d"),),
        hooks=Hooks((RuntimeHook(HookEvent.PRE_TOOL_USE, "Edit", Executable(elsewhere, "e")),), ()),
        trace=(),
    )

    with pytest.raises(ValueError, match="outside every composed skill"):
        AgySurface().plan(
            composition,
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=tmp_path / "sessions" / "s",
            layout=layout,
            host=probe_host(),
        )


# --- the launch shape --------------------------------------------------------


def test_system_prompt_omits_skills_index(agy_project) -> None:
    asm, _composition = agy_project
    result = compose_to_run(asm, "test-role")

    prompt = AgySurface().build_system_prompt(result)

    assert "## AVAILABLE SKILLS" not in prompt


def test_get_env_carries_no_dead_rules_path(agy_project, tmp_path) -> None:
    asm, _ = agy_project

    env = AgySurface().get_env(tmp_path / "sess", asm.layout)

    assert "GEMINI_CLI_PROJECT_RULES_PATH" not in env


def test_get_run_command_headless_skips_trust() -> None:
    cmd = AgySurface().get_run_command(["agy"], "do it")

    assert "-p" in cmd
    assert cmd[-1] == "do it"


def test_execution_context_is_clean_no_op_native_by_default(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    gemini = project / "GEMINI.md"
    agents = project / "AGENTS.md"
    gemini.write_text("root gemini rules")
    agents.write_text("root agents rules")

    provider = AgySurface()
    with provider.execution_context(ProjectLayout.at(project)):
        assert gemini.exists()
        assert agents.exists()
        assert not any(p.name.startswith(".GEMINI.md.ai_hats_bak_") for p in project.iterdir())

    assert gemini.is_file()
    assert agents.is_file()


def test_provider_name() -> None:
    assert AgySurface().name == "agy"


def test_system_prompt_path(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert AgySurface().system_prompt_path(ProjectLayout.at(project)) == gemini_md(project)


def test_rules_dir(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    assert AgySurface().rules_dir(session_dir) == session_dir / "rules"


def test_get_cli_command() -> None:
    provider = AgySurface()
    assert provider.get_cli_command() == ["agy"]
    assert provider.get_cli_command(["--foo", "bar"]) == ["agy", "--foo", "bar"]


def test_get_cli_launch_args_translates_positional_prompt() -> None:
    provider = AgySurface()
    base_cmd = ["agy", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == [
        "agy",
        "-i",
        "hello world",
        "--add-dir",
        "/path/to/rules",
    ]


def test_get_cli_launch_args_preserves_existing_prompt_flag() -> None:
    provider = AgySurface()
    base_cmd = ["agy", "-i", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == base_cmd


def test_get_cli_launch_args_with_model_flag_and_positional_prompt() -> None:
    provider = AgySurface()
    base_cmd = ["agy", "--model", "gemini-2.5-pro", "hello world", "--add-dir", "/path/to/rules"]
    assert provider.get_cli_launch_args(base_cmd, "sid-1", False) == [
        "agy",
        "-i",
        "hello world",
        "--model",
        "gemini-2.5-pro",
        "--add-dir",
        "/path/to/rules",
    ]


def test_get_run_command_with_harness_flags() -> None:
    provider = AgySurface()
    flags = provider.model_flags("gemini-2.5-pro")
    cmd = provider.get_run_command(["agy"] + flags, "task prompt")
    assert cmd == [
        "agy",
        "--model",
        "gemini-2.5-pro",
        "--output-format",
        "json",
        "-p",
        "task prompt",
    ]


def test_get_env(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    session_dir = tmp_path / "session"
    env = AgySurface().get_env(session_dir, ProjectLayout.at(project))
    assert env["AI_HATS_PROJECT_DIR"] == str(project)
    assert env["AI_HATS_DIR"] == str(project / ".agent" / "ai-hats")


def test_agy_provider_detected_home_dirs() -> None:
    provider = AgySurface()
    assert ".gemini" in provider.detected_home_dirs()
    assert ".agy" in provider.detected_home_dirs()
