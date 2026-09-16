"""Contract tests for ``ClineSurface``.

Pure-method assertions (no real cline, no auth): the CLI shape, the env, the
inline ``-s`` role delivery, and the session cline is planned (ADR-0036) on the
clean-root invariant — skills mirror into ``<cache_root>/sessions/<sid>/skills``
(delivered via ``--config``) and the composed hook chain into ``<sid>/hooks``
(delivered via ``--hooks-dir``), never into the project root.
"""

from __future__ import annotations

import dataclasses
import json
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.materialization import WriteKind
from ai_hats.materialize import compose_to_run
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import adapt
from ai_hats.surfaces.cline import ClineSurface
from ai_hats.surfaces.cline.runtime_hooks import HOOK_SHIMS, shim_source
from ai_hats.surfaces.hook_channel import HookEvent
from ai_hats.surfaces.plan import (
    CompositionPlan,
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


def _fake_result(skills: list[Path] | None = None) -> SimpleNamespace:
    """A minimal duck-typed ``CompositionResult`` for ``_compose_sections``."""
    skill_objs = []
    if skills:
        for p in skills:
            skill_objs.append(SimpleNamespace(name=p.name, source_path=p))
    return SimpleNamespace(
        priorities=["Reliability"],
        merged_injection="## ROLE\nbody",
        rules=[],
        skills=skill_objs,
        checks=(),
    )


def _make_skill(tmp_path: Path, name: str, body: str = "instructions") -> Path:
    """Create a fake skill source dir with a SKILL.md."""
    d = tmp_path / "sources" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n{body}\n")
    return d


_PLAIN_SKILL_MD = "---\nname: {name}\ndescription: test\n---\n{body}\n"
_HOOKED_SKILL_MD = (
    "---\n"
    "name: guard\n"
    "description: test guard\n"
    "ai_hats:\n"
    "  runtime_hooks:\n"
    "    PreToolUse:\n"
    "      - matcher: Bash\n"
    "        script: hooks/guard.sh\n"
    "    PostToolUse:\n"
    "      - matcher: Edit|Write\n"
    "        script: hooks/guard.sh\n"
    "---\n"
    "guard\n"
)
_GUARD_SCRIPT = ("hooks/guard.sh", "#!/usr/bin/env bash\nexit 0\n")


def _library(
    root: Path,
    skills: dict[str, str],
    scripts: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """A library with the given skills and one role composing them all."""
    lib = root / "lib"
    for name, skill_md in skills.items():
        skill_dir = lib / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill_md)
        if scripts and name in scripts:
            relpath, body = scripts[name]
            script = skill_dir / relpath
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(body)
            script.chmod(0o755)
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Reliability\n"
        f"composition:\n  skills: [{', '.join(skills)}]\n"
        "injection: '## ROLE\\nbody'\n"
    )
    return lib


def _compose(
    project: Path, lib: Path, diagnostics: list | None = None
) -> tuple[Assembler, CompositionPlan]:
    ProjectConfig(provider="cline", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
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
def cline_project(tmp_path, monkeypatch):
    """Minimal library + role composed for the cline surface, the person's home pinned."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(tmp_path, {"s": _PLAIN_SKILL_MD.format(name="s", body="# body")})
    return _compose(project, lib)


@pytest.fixture
def hooked_project(tmp_path, monkeypatch):
    """A role whose one skill declares a runtime hook on both events cline has."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(tmp_path, {"guard": _HOOKED_SKILL_MD}, {"guard": _GUARD_SCRIPT})
    return _compose(project, lib)


def _plan(asm, composition, root: Path, run_mode: RunMode, policy: SessionPolicy | None = None):
    surface = ClineSurface()
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


def test_name_is_cline() -> None:
    assert ClineSurface().name == "cline"


def test_hitl_children_inherit_authoritative_session_path() -> None:
    assert ClineSurface().supports_session_command_wrappers()


def test_get_cli_command_is_bare_binary() -> None:
    # Bare base so the HITL `-i` (added at launch) and the automate
    # `--yolo` (added by get_run_command) never collide.
    provider = ClineSurface()
    assert provider.get_cli_command() == ["cline"]
    assert provider.get_cli_command(["-c", "sub/dir"]) == ["cline", "-c", "sub/dir"]


def test_get_run_command_is_headless_yolo() -> None:
    cmd = ClineSurface().get_run_command(["cline"], "do the thing")
    assert cmd == ["cline", "--yolo", "--json", "do the thing"]
    # mutually-exclusive interactive flag must never appear on the headless path
    assert "-i" not in cmd and "--tui" not in cmd
    # ai-hats-wt owns isolation — cline must not fork its own worktree
    assert "--worktree" not in cmd


def test_get_run_command_threads_model() -> None:
    provider = ClineSurface()
    flags = provider.model_flags("glm-5.2")
    cmd = provider.get_run_command(["cline"] + flags, "task")
    # model flag gets sorted with the command prefix before the meta prompt
    assert cmd == ["cline", "--model", "glm-5.2", "--yolo", "--json", "task"]
    # task prompt stays positional-last
    assert cmd[-1] == "task"


def test_get_run_command_drops_stale_interactive_base() -> None:
    # Even if a `-i` base leaks in, the headless rebuild strips it.
    cmd = ClineSurface().get_run_command(["cline", "-i"], "task")
    assert cmd == ["cline", "--yolo", "--json", "task"]


def test_get_run_command_preserves_passthrough_args() -> None:
    # Non-interactive passthrough (e.g. the automate --config) survives the rebuild.
    cmd = ClineSurface().get_run_command(["cline", "--config", "/x"], "task")
    assert cmd == ["cline", "--config", "/x", "--yolo", "--json", "task"]


# ---- get_env ---------------------------------------------------


def test_get_env_pins_cline_data_dir(tmp_path, monkeypatch) -> None:
    # --config relocates cline's base dir → data (auth/sessions/db)
    # must be pinned back to the real cline home, else auth is lost.
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    env = ClineSurface().get_env(tmp_path / "session", ProjectLayout.at(tmp_path))
    assert env["CLINE_DATA_DIR"] == str(tmp_path / "home" / ".cline" / "data")
    # R7: AI_HATS_DIR is needed by runtime hooks / skills.
    assert env["AI_HATS_DIR"]
    assert env["AI_HATS_PROJECT_DIR"] == str(tmp_path)
    # Per-session hub port to avoid EADDRINUSE on parallel sessions.
    assert env["CLINE_HUB_PORT"]
    # The dead TS plugin is dropped — no root plugins dir to point at.
    assert "CLINE_HOOKS_DIR" not in env


def test_claim_launch_env_sets_cline_hub_port(tmp_path) -> None:
    # Per-session CLINE_HUB_PORT moves each ai-hats cline session off
    # the default hub port (25463) so parallel sessions don't collide.
    env = ClineSurface().claim_launch_env(tmp_path / "session", ProjectLayout.at(tmp_path))
    port = int(env["CLINE_HUB_PORT"])
    assert 1024 < port < 65536


def test_claim_launch_env_distinct_sessions_distinct_ports(tmp_path) -> None:
    # Two sessions must own different hub ports (ephemeral allocation).
    env_a = ClineSurface().claim_launch_env(tmp_path / "sess-a", ProjectLayout.at(tmp_path))
    env_b = ClineSurface().claim_launch_env(tmp_path / "sess-b", ProjectLayout.at(tmp_path))
    assert env_a["CLINE_HUB_PORT"] != env_b["CLINE_HUB_PORT"]


def test_get_env_names_the_port_without_taking_one(tmp_path, monkeypatch) -> None:
    """get_env is on the report path too, where binding is a side effect."""
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("get_env opened a socket"))
    env = ClineSurface().get_env(tmp_path / "session", ProjectLayout.at(tmp_path))

    assert env["CLINE_HUB_PORT"] == "<assigned at launch>"


def test_update_system_prompt_is_noop(tmp_path) -> None:
    # Inline-only surface: set_role must not litter a CLINE.md cline ignores.
    ClineSurface().update_system_prompt(ProjectLayout.at(tmp_path), "role body")
    assert not (tmp_path / "CLINE.md").exists()


def test_build_system_prompt_composes_sections() -> None:
    out = ClineSurface().build_system_prompt(_fake_result())
    assert "## PRIORITIES" in out
    assert "1. Reliability" in out
    assert "## ROLE" in out


def test_build_system_prompt_suppresses_skills_index(tmp_path) -> None:
    # Skills delivered via the native <cache>/skills registry, so the
    # composed sections carry no text index (the skill-index toggle was removed).
    skill_path = _make_skill(tmp_path, "my-skill")
    out = ClineSurface().build_system_prompt(_fake_result(skills=[skill_path]))
    assert "## AVAILABLE SKILLS" not in out


# ---- the plan (ADR-0036 D2) ----------------------------------------------


def test_a_hitl_plan_hands_the_role_inline_and_the_cache_as_config(cline_project, tmp_path):
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"
    provider = ClineSurface()

    plan = _plan(asm, composition, root, RunMode.HITL)

    # Role inline via -s; -i is launch mode, not context, and rides the launch-args seam.
    assert plan.launch.args == ("-s", plan.prompt.text, "--config", str(root))
    assert "-i" in provider.get_cli_launch_args(["cline", *plan.launch.args], "sid-1", False)
    assert "## PRIORITIES" in plan.prompt.text and "## ROLE" in plan.prompt.text
    assert "## AVAILABLE SKILLS" not in plan.prompt.text, "cline discovers skills natively"
    # No context file: the -s value IS the persisted meta-prompt bytes (symmetry).
    assert plan.context is None and context_text(plan) == plan.prompt.text
    assert set(plan.env) == {
        "AI_HATS_DIR",
        "AI_HATS_PROJECT_DIR",
        "CLINE_HUB_PORT",
        "CLINE_DATA_DIR",
    }
    assert plan.env["CLINE_HUB_PORT"] == "<assigned at launch>"
    validate(plan)


def test_a_sub_agent_plan_adds_no_flag_and_hands_the_role_in_the_prompt_token(
    cline_project, tmp_path
):
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.AUTOMATE)

    assert plan.launch.args == ("--config", str(root)) and plan.context is None
    launched = ClineSurface().automate_launch(
        plan, _flags(root, brief="# TASK\ndo it"), {}, layout=asm.layout
    )
    assert launched.args[:-1] == ("cline", "--config", str(root), "--yolo", "--json")
    assert launched.args[-1].startswith(plan.prompt.text)
    assert launched.args[-1].endswith("# TASK\ndo it")
    assert launched.prompt == launched.args[-1]


def test_two_roots_are_two_configs(cline_project, tmp_path):
    asm, composition = cline_project

    a = _plan(asm, composition, tmp_path / "sessions" / "sid-a", RunMode.HITL)
    b = _plan(asm, composition, tmp_path / "sessions" / "sid-b", RunMode.HITL)

    cfg_a = a.launch.args[a.launch.args.index("--config") + 1]
    cfg_b = b.launch.args[b.launch.args.index("--config") + 1]
    assert cfg_a != cfg_b
    assert "sid-a" in cfg_a and "sid-b" in cfg_b


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_skills_mirror_into_the_root_in_both_modes(tmp_path, monkeypatch, run_mode):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(
        tmp_path,
        {
            "skill-a": _PLAIN_SKILL_MD.format(name="skill-a", body="a"),
            "skill-b": _PLAIN_SKILL_MD.format(name="skill-b", body="b"),
        },
    )
    asm, composition = _compose(project, lib)
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, run_mode)
    apply(plan)

    assert "--config" in plan.launch.args
    assert (root / "skills" / "skill-a" / "SKILL.md").is_file()
    assert (root / "skills" / "skill-b" / "SKILL.md").is_file()


def test_the_project_root_stays_clean(cline_project, tmp_path):
    # Clean-root: no .cline/ and no .gitignore mutation in the root.
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"
    gitignore = (asm.layout.root / ".gitignore").read_text()

    apply(_plan(asm, composition, root, RunMode.HITL))

    assert not (asm.layout.root / ".cline").exists()
    assert (asm.layout.root / ".gitignore").read_text() == gitignore


def test_context_off_drops_the_inline_role_and_keeps_the_config(cline_project, tmp_path):
    # Only-seam filtering (supervisor): policy.context=False → no -s role delivery.
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL, SessionPolicy(context=False))

    assert "-s" not in plan.launch.args
    # skills category is unaffected by policy
    assert plan.launch.args == ("--config", str(root))


def test_a_hookless_role_adds_no_settings_or_hooks_flag(cline_project, tmp_path):
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL)

    assert "--settings" not in plan.launch.args
    assert "--hooks-dir" not in plan.launch.args
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env
    assert not any(e.kind is WriteKind.WRITE_EXECUTABLE for e in plan.entries)


def test_two_sessions_mirror_their_own_skills(tmp_path, monkeypatch):
    # Each session owns its own mirror — no cross-session sharing, so no
    # refcount/lock dance is needed.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(
        tmp_path,
        {
            "skill-a": _PLAIN_SKILL_MD.format(name="skill-a", body="a"),
            "skill-b": _PLAIN_SKILL_MD.format(name="skill-b", body="b"),
        },
    )
    asm, composition = _compose(project, lib)
    by_name = {skill.name: skill for skill in composition.skills}
    only_a = dataclasses.replace(composition, skills=(by_name["skills::skill-a"],))
    only_b = dataclasses.replace(composition, skills=(by_name["skills::skill-b"],))
    root_1, root_2 = tmp_path / "sessions" / "sid-1", tmp_path / "sessions" / "sid-2"

    apply(_plan(asm, only_a, root_1, RunMode.AUTOMATE))
    apply(_plan(asm, only_b, root_2, RunMode.AUTOMATE))

    assert (root_1 / "skills" / "skill-a").is_dir()
    assert not (root_1 / "skills" / "skill-b").exists()
    assert (root_2 / "skills" / "skill-b").is_dir()


def test_applying_the_plan_twice_changes_nothing(cline_project, tmp_path):
    """The primitive-level counter lives in the stage acceptance test, where
    the ``writes`` fixture is; here the outcome the record carries."""
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"
    plan = _plan(asm, composition, root, RunMode.HITL)
    assert apply(plan).changed is True
    first = sorted(p.name for p in (root / "skills").iterdir())

    assert apply(plan).changed is False
    assert sorted(p.name for p in (root / "skills").iterdir()) == first == ["s"]


def test_the_document_expands_the_fsm_edges_token(tmp_path, monkeypatch):
    """The token is carried by a CORE library skill (hatrack), so a surface
    that skipped it shipped a SKILL.md whose own prose called the missing table
    authoritative. Delivery is what this pins — the renderer is tested upstream."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(
        tmp_path,
        {
            "fsm-skill": _PLAIN_SKILL_MD.format(
                name="fsm-skill", body="edges:\n\n{{backlog_fsm_edges}}"
            )
        },
    )
    asm, composition = _compose(project, lib)
    root = tmp_path / "sessions" / "s1"

    apply(_plan(asm, composition, root, RunMode.AUTOMATE))

    delivered = (root / "skills" / "fsm-skill" / "SKILL.md").read_text()
    assert "{{backlog_fsm_edges}}" not in delivered
    assert "brainstorm" in delivered  # a real FSM state reached the file


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_application(
    cline_project, tmp_path, run_mode
):
    asm, composition = cline_project
    root = tmp_path / "sessions" / "s1"

    before = _plan(asm, composition, root, run_mode)
    apply(before)
    after = _plan(asm, composition, root, run_mode)

    assert before == after and before.digest == after.digest
    assert apply(after).changed is False
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _plan(asm, more, root, run_mode) != before, "a changed input must show"


# ---- hooks -----------------------------------------------------------------


def test_a_plan_delivers_the_composed_runtime_hooks(hooked_project, tmp_path):
    asm, composition = hooked_project
    root = tmp_path / "sessions" / "sid-hooks"
    host = probe_host(surface=ClineSurface())

    plan = _plan(asm, composition, root, RunMode.HITL)

    hooks_dir = root / "hooks"
    assert plan.launch.args[-2:] == ("--hooks-dir", str(hooks_dir))
    shims = [e for e in plan.entries if e.kind is WriteKind.WRITE_EXECUTABLE]
    assert [e.target for e in shims] == [hooks_dir / name for name in HOOK_SHIMS]
    assert plan.env["AI_HATS_SESSION_CACHE_DIR"] == str(root)
    assert plan.env["AI_HATS_PYTHON"] == str(host.python)
    apply(plan)
    for name in HOOK_SHIMS:
        assert (hooks_dir / name).stat().st_mode & 0o111, name
        assert (hooks_dir / name).read_text() == shim_source(name)
    manifest = json.loads((root / "hooks.json").read_text())
    mirrored = str(root / "skills" / "guard" / "hooks" / "guard.sh")
    assert manifest["session"] == {"id": "sid-hooks", "ai_hats_dir": str(asm.layout.base)}
    assert [h["command"] for h in manifest["hooks"]["PreToolUse"]] == [mirrored]
    assert [h["command"] for h in manifest["hooks"]["PostToolUse"]] == [mirrored]
    assert Path(mirrored).stat().st_mode & 0o111, "the mirror keeps the script's mode"
    assert not (asm.layout.root / ".cline").exists()


def test_a_sub_agent_plan_delivers_the_hooks_too(hooked_project, tmp_path):
    asm, composition = hooked_project
    root = tmp_path / "sessions" / "sid-automate-hooks"

    plan = _plan(asm, composition, root, RunMode.AUTOMATE)

    assert plan.launch.args == ("--config", str(root), "--hooks-dir", str(root / "hooks"))
    assert any(e.target == root / "hooks.json" for e in plan.entries)


def test_hooks_off_delivers_no_chain(hooked_project, tmp_path):
    asm, composition = hooked_project
    root = tmp_path / "sessions" / "s1"

    plan = _plan(asm, composition, root, RunMode.HITL, SessionPolicy(hooks=False))

    assert "--hooks-dir" not in plan.launch.args
    assert not any(e.target.name == "hooks.json" for e in plan.entries)
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env


def test_the_packaged_shims_are_exactly_the_ones_the_plan_names():
    """The plan writes the shims by name; a shim added to the package without
    a name here would ship with the wheel and never reach a session."""
    packaged = {entry.name for entry in files("ai_hats.surfaces.cline").joinpath("hooks").iterdir()}
    assert packaged == set(HOOK_SHIMS)
    for name in HOOK_SHIMS:
        assert shim_source(name).startswith("#!/usr/bin/env bash\n")
        assert f"hook_dispatcher {name}" in shim_source(name)


def test_a_script_missing_from_the_skill_is_a_diagnostic_not_a_silent_drop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    lib = _library(tmp_path, {"guard": _HOOKED_SKILL_MD}, {"guard": _GUARD_SCRIPT})
    (lib / "skills" / "guard" / "hooks" / "guard.sh").unlink()  # safe-delete: ok tmp-fixture
    diagnostics: list = []
    asm, composition = _compose(project, lib, diagnostics)
    root = tmp_path / "sessions" / "sid-gone"

    plan = _plan(asm, composition, root, RunMode.HITL)

    assert composition.hooks.runtime == ()
    assert "--hooks-dir" not in plan.launch.args
    assert not any(e.target.name == "hooks.json" for e in plan.entries)
    notices = [d.render() for d in diagnostics if "guard" in d.render()]
    assert len(notices) == 2, "one per declared event, both pointing at the same file"
    assert all("hooks/guard.sh" in n and "will not run" in n for n in notices)


def test_a_hook_outside_every_composed_skill_refuses_the_plan(tmp_path):
    """A mirror holds only composed skills, so a script elsewhere has no
    command to point at — refused at planning, not wired in silence."""
    layout = ProjectLayout.at(tmp_path / "proj")
    skill_dir = tmp_path / "lib" / "skills" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")
    elsewhere = tmp_path / "elsewhere.sh"
    elsewhere.write_text("#!/bin/sh\n")
    from ai_hats.fs_digest import dir_digest

    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(Skill("skills::s", skill_dir, dir_digest(skill_dir), "# body\n", ()),),
        hooks=Hooks(
            runtime=(
                RuntimeHook(
                    at=HookEvent.PRE_TOOL_USE,
                    matcher="Bash",
                    run=Executable(path=elsewhere, content_digest="0" * 64),
                ),
            ),
            external=(),
        ),
        trace=(),
    )
    surface = ClineSurface()

    with pytest.raises(ValueError, match="outside every composed skill"):
        surface.plan(
            composition,
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=tmp_path / "sessions" / "s1",
            layout=layout,
            host=probe_host(surface=surface),
        )


# -- resolve_transcript ------------------------------------------


def test_resolve_transcript_returns_none_when_dir_absent(tmp_path, monkeypatch) -> None:
    """No ~/.cline/data/sessions/ → [] (cline not installed / never run)."""
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    provider = ClineSurface()
    assert provider.resolve_transcript(tmp_path, "20260720-120000-1") == []


def test_resolve_transcript_finds_recent_messages_json(tmp_path, monkeypatch) -> None:
    """mtime-window: a .messages.json with mtime >= session start is found."""
    import os

    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    home = tmp_path / "home"
    sessions_dir = home / ".cline" / "data" / "sessions"
    sid_dir = sessions_dir / "abc123"
    sid_dir.mkdir(parents=True)
    msg = sid_dir / "abc123.messages.json"
    msg.write_text('{"messages": []}')
    future_ns = 1_900_000_000 * 1_000_000_000  # ~2030
    os.utime(msg, ns=(future_ns, future_ns))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    provider = ClineSurface()
    found = provider.resolve_transcript(tmp_path, "20260720-120000-1")
    assert found == [msg]


def test_resolve_transcript_skips_older_messages_json(tmp_path, monkeypatch) -> None:
    """A .messages.json with mtime BEFORE session start is not picked."""
    import os

    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    home = tmp_path / "home"
    sessions_dir = home / ".cline" / "data" / "sessions"
    sid_dir = sessions_dir / "old"
    sid_dir.mkdir(parents=True)
    msg = sid_dir / "old.messages.json"
    msg.write_text('{"messages": []}')
    old_ns = 1_577_836_800 * 1_000_000_000  # 2020-01-01
    os.utime(msg, ns=(old_ns, old_ns))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    provider = ClineSurface()
    found = provider.resolve_transcript(tmp_path, "20260720-120000-1")
    assert found == []
