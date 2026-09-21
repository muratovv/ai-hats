"""Contract tests for the OpenCode surface: a session planned from the
composition half and the probed home, then applied."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy, working_directory_section
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import apply, validate
from ai_hats.surfaces.opencode import OpenCodeSurface
from ai_hats.surfaces.opencode.home import OpenCodeHome, probe_home
from ai_hats.surfaces.opencode.provider import AGENT_NAME, ENV_OPENCODE_CONFIG
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Hooks,
    Host,
    LaunchFlags,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
    Skill,
)

SESSION_ID = "20260822-000000-1-00000"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    # A base with no opencode/ dir: nothing of the person's is projected.
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def _skill(tmp_path: Path, name: str, description: str = "Test skill", *, scripts=False) -> Skill:
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True)
    document = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nInstructions.\n"
    (source / "SKILL.md").write_text(document)
    on_path: tuple[str, ...] = ()
    if scripts:
        (source / "scripts").mkdir()
        (source / "scripts" / "tool.sh").write_text("#!/bin/sh\ntrue\n")
        on_path = ("scripts",)
    return Skill(
        name=f"skills::{name}",
        path=source.resolve(),
        content_digest=dir_digest(source),
        document=document,
        on_path=on_path,
    )


def _composition(
    *skills: Skill, rule: str | None = None, identity: str = "maintainer"
) -> CompositionPlan:
    blocks = [
        PromptBlock("PRIORITIES", (PromptMember("test::priorities", "1. Reliability", None),)),
        PromptBlock(
            None,
            (PromptMember("test::prompt", "## ROLE\nYou are the repository maintainer.", None),),
        ),
    ]
    if rule is not None:
        blocks.append(
            PromptBlock("RULES", (PromptMember("rules::safe-edits", rule, "safe-edits"),))
        )
    return CompositionPlan(
        identity=identity,
        prompt=Prompt(tuple(blocks)),
        skills=skills,
        hooks=Hooks((), ()),
        trace=(),
    )


def _layout(tmp_path: Path) -> ProjectLayout:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return ProjectLayout.at(project)


def _plan(
    layout: ProjectLayout,
    composition: CompositionPlan,
    *,
    run_mode: RunMode = RunMode.HITL,
    policy: SessionPolicy = SessionPolicy(),
) -> MaterializationPlan:
    surface = OpenCodeSurface()
    return surface.plan(
        composition,
        run_mode=run_mode,
        policy=policy,
        root=layout.cache.session(SESSION_ID),
        layout=layout,
        host=probe_host(surface=surface),
    )


def _document(plan: MaterializationPlan) -> dict:
    entry = next(e for e in plan.entries if e.kind is WriteKind.MERGE_JSON)
    return json.loads(entry.bytes or b"{}")


def _snapshot(project: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(project)): path.read_bytes()
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def test_provider_identity_and_session_cache_contract(tmp_path: Path) -> None:
    provider = OpenCodeSurface()
    assert provider.name == "opencode"
    assert provider.detected_home_dirs() == [".opencode"]
    assert provider.system_prompt_path(ProjectLayout.at(tmp_path)) is None
    assert provider.rules_dir(tmp_path / "session") == tmp_path / "session" / "rules"
    # Consent middleware is PATH-based and provider-agnostic; opencode opts in
    # so roles declaring consent points are enforceable on this surface.
    assert provider.supports_session_command_wrappers() is True


def test_planning_without_the_probed_home_is_refused(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    host = Host(python=Path("/opt/py"), path="/usr/bin", commands={})

    with pytest.raises(RuntimeError, match="probe_host"):
        OpenCodeSurface().plan(
            _composition(),
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=layout.cache.session(SESSION_ID),
            layout=layout,
            host=host,
        )


def test_context_hitl_plans_the_session_agent(tmp_path: Path) -> None:
    provider = OpenCodeSurface()
    layout = _layout(tmp_path)
    composition = _composition(
        _skill(tmp_path, "hatrack", "Drive the backlog"),
        rule="Never bypass repository safety checks.",
    )

    plan = _plan(layout, composition)

    config_path = provider.session_config_path(layout, SESSION_ID)
    assert plan.env[ENV_OPENCODE_CONFIG] == str(config_path)
    assert plan.launch.args == ("--agent", AGENT_NAME)
    assert plan.context is None, "the prompt rides the config document, not a file"
    agent = _document(plan)["agent"][AGENT_NAME]
    assert agent["mode"] == "primary"
    assert "repository maintainer" in agent["prompt"]
    assert "## RULES" in agent["prompt"]
    assert "hatrack" in agent["prompt"], "skill index with cache paths must ride the prompt"
    assert str(provider.session_skills_root(layout, SESSION_ID)) in agent["prompt"]
    assert agent["prompt"] == plan.prompt.text

    apply(plan)
    assert json.loads(config_path.read_text())["agent"][AGENT_NAME] == agent


def test_the_agent_names_the_expression_the_session_composed(tmp_path: Path) -> None:
    """The builder named the role; the plan names the identity, which is the
    same word for a plain role and the whole expression for a runtime one."""
    plan = _plan(_layout(tmp_path), _composition(identity="maintainer + sre"))

    description = _document(plan)["agent"][AGENT_NAME]["description"]
    assert description == "ai-hats composed role session (maintainer + sre)"


def test_context_config_carries_no_permission_keys(tmp_path: Path) -> None:
    """Work policy lives in the role's manifest, not the config."""
    plan = _plan(_layout(tmp_path), _composition(_skill(tmp_path, "hatrack")))

    document = _document(plan)
    assert "permission" not in document, "generated config must stay free of policy keys"
    assert set(document) == {"$schema", "agent", "plugin"}


def test_a_config_left_off_by_policy_is_not_written(tmp_path: Path) -> None:
    plan = _plan(
        _layout(tmp_path),
        _composition(_skill(tmp_path, "hatrack")),
        policy=SessionPolicy(context=False, hooks=False),
    )

    assert not [e for e in plan.entries if e.kind is WriteKind.MERGE_JSON]
    assert plan.launch.args == () and ENV_OPENCODE_CONFIG not in plan.env


def test_the_config_merge_keeps_a_persons_keys(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan(layout, _composition(_skill(tmp_path, "hatrack")))
    config_path = OpenCodeSurface().session_config_path(layout, SESSION_ID)
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"theme": "dark"}\n')

    apply(plan)

    document = json.loads(config_path.read_text())
    assert document["theme"] == "dark"
    assert set(document) == {"theme", "$schema", "agent", "plugin"}


def test_applying_the_plan_writes_nothing_into_project_root(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    before = _snapshot(layout.root)

    apply(_plan(layout, _composition(_skill(tmp_path, "hatrack"))))

    assert _snapshot(layout.root) == before, "clean-root invariant violated"


def test_the_plan_writes_only_under_its_root(tmp_path: Path) -> None:
    plan = _plan(_layout(tmp_path), _composition(_skill(tmp_path, "hatrack")))

    validate(plan)
    assert all(e.target.is_relative_to(plan.root) for e in plan.entries)
    assert not any(e.escape for e in plan.entries)


def test_skills_mirror_lands_in_session_cache_with_path_env(tmp_path: Path) -> None:
    provider = OpenCodeSurface()
    layout = _layout(tmp_path)
    skill = _skill(tmp_path, "hatrack", scripts=True)

    plan = _plan(layout, _composition(skill))
    apply(plan)

    skills_root = provider.session_skills_root(layout, SESSION_ID)
    assert (skills_root / "hatrack" / "SKILL.md").is_file()
    parts = plan.env["PATH"].split(":")
    assert parts[0] == str(skills_root / "hatrack" / "scripts")
    # The mirror is the whole tree; the library's own scripts dir stays off
    # PATH, so a session never runs bytes it did not mirror.
    assert str(skill.path / "scripts") not in parts


def test_skills_mirror_is_natively_discoverable_via_xdg(tmp_path: Path) -> None:
    """The mirror lives under the redirected config dir."""
    provider = OpenCodeSurface()
    layout = _layout(tmp_path)

    plan = _plan(layout, _composition(_skill(tmp_path, "hatrack")))
    apply(plan)

    xdg_root = provider.session_xdg_config_home(layout, SESSION_ID)
    assert (xdg_root / "opencode" / "skills" / "hatrack" / "SKILL.md").is_file(), (
        "mirror must sit on a native discovery path"
    )
    assert plan.env["XDG_CONFIG_HOME"] == str(xdg_root)


def test_a_missing_config_home_projects_nothing(tmp_path: Path) -> None:
    home = probe_home({"AI_HATS_OPENCODE_CONFIG_HOME": str(tmp_path / "config-home")})

    assert home == OpenCodeHome(
        config_dir=tmp_path / "config-home" / "opencode", resolved=None, entries=(), skills=()
    )


def test_a_relative_config_home_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="absolute"):
        probe_home({"AI_HATS_OPENCODE_CONFIG_HOME": "relative/config"})


def test_the_probed_home_lists_what_the_session_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base-config"
    base_opencode = base / "opencode"
    (base_opencode / "skills" / "user-own-skill").mkdir(parents=True)
    (base_opencode / "opencode.jsonc").write_text("{}")
    (base_opencode / "plugins").mkdir()

    home = probe_home({"AI_HATS_OPENCODE_CONFIG_HOME": str(base)})

    assert home.config_dir == base_opencode and home.resolved == base_opencode.resolve()
    assert home.entries == ("opencode.jsonc", "plugins"), "skills is the session's own"
    assert home.skills == ("user-own-skill",)


def test_base_config_home_is_projected_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User-owned entries stay reachable via symlinks, never written."""
    base = tmp_path / "base-config"
    base_opencode = base / "opencode"
    (base_opencode / "skills" / "user-own-skill").mkdir(parents=True)
    (base_opencode / "skills" / "user-own-skill" / "SKILL.md").write_text(
        "---\nname: user-own-skill\ndescription: x\n---\nbody\n"
    )
    (base_opencode / "opencode.jsonc").write_text('{"$schema": "https://opencode.ai/config.json"}')
    (base_opencode / "plugins").mkdir()
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))
    provider = OpenCodeSurface()
    layout = _layout(tmp_path)

    plan = _plan(layout, _composition(_skill(tmp_path, "hatrack")))
    apply(plan)

    session_dir = provider.session_xdg_config_home(layout, SESSION_ID) / "opencode"
    assert (session_dir / "opencode.jsonc").is_symlink()
    assert (session_dir / "plugins").is_symlink()
    assert not (session_dir / "skills").is_symlink(), "skills dir is session-owned"

    skills_dir = provider.session_skills_root(layout, SESSION_ID)
    assert (skills_dir / "hatrack" / "SKILL.md").is_file(), "composed mirror is real files"
    assert (skills_dir / "user-own-skill").is_symlink(), "non-shadowed user skills projected"
    assert (base_opencode / "opencode.jsonc").read_text().startswith("{"), "base untouched"


def test_composed_skill_shadows_same_named_user_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base-config"
    base_skill = base / "opencode" / "skills" / "hatrack"
    base_skill.mkdir(parents=True)
    (base_skill / "SKILL.md").write_text("---\nname: hatrack\ndescription: user\n---\nuser\n")
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))
    provider = OpenCodeSurface()
    layout = _layout(tmp_path)

    plan = _plan(layout, _composition(_skill(tmp_path, "hatrack", "composed wins")))
    apply(plan)

    skills_dir = provider.session_skills_root(layout, SESSION_ID)
    assert not (skills_dir / "hatrack").is_symlink()
    assert "composed wins" in (skills_dir / "hatrack" / "SKILL.md").read_text()


def test_a_config_home_inside_the_session_xdg_root_is_refused(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    provider = OpenCodeSurface()
    session_dir = provider.session_xdg_config_home(layout, SESSION_ID) / "opencode"
    inside = OpenCodeHome(config_dir=session_dir, resolved=session_dir, entries=("x",), skills=())
    host = dataclasses.replace(probe_host(surface=provider), home=inside)

    with pytest.raises(RuntimeError, match="outside the session XDG root"):
        provider.plan(
            _composition(_skill(tmp_path, "hatrack")),
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=layout.cache.session(SESSION_ID),
            layout=layout,
            host=host,
        )


def test_skillsless_role_pins_no_xdg(tmp_path: Path) -> None:
    plan = _plan(_layout(tmp_path), _composition())

    assert "XDG_CONFIG_HOME" not in plan.env and "PATH" not in plan.env
    assert not [e for e in plan.entries if e.kind is WriteKind.COPY_TREE]


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_it_is_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_mode: RunMode
) -> None:
    base = tmp_path / "base-config"
    (base / "opencode" / "skills" / "personal").mkdir(parents=True)
    (base / "opencode" / "opencode.jsonc").write_text("{}")
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))
    layout = _layout(tmp_path)
    composition = _composition(_skill(tmp_path, "hatrack"))

    before = _plan(layout, composition, run_mode=run_mode)
    apply(before)
    after = _plan(layout, composition, run_mode=run_mode)

    assert before == after and before.digest == after.digest
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _plan(layout, more, run_mode=run_mode) != before, "a changed input must show"


def test_applying_the_plan_twice_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base-config"
    (base / "opencode" / "skills" / "personal").mkdir(parents=True)
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(base))
    plan = _plan(_layout(tmp_path), _composition(_skill(tmp_path, "hatrack")))

    first = apply(plan)
    second = apply(plan)

    assert first.changed is True and second.changed is False
    assert {a.outcome.value for a in second.entries} == {"unchanged"}


def test_automate_launch_runs_the_agent_headless_with_the_prompt_in_one_token(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    plan = _plan(layout, _composition(_skill(tmp_path, "hatrack")), run_mode=RunMode.AUTOMATE)
    flags = LaunchFlags(
        session_id=SESSION_ID,
        session_dir=layout.sessions.runs / SESSION_ID,
        trace_path="t",
        root_pid="1",
        provider_session_id=None,
        model="openai/gpt-5",
        brief="# TASK\ndemo",
    )

    launched = OpenCodeSurface().automate_launch(plan, flags, {}, layout=layout)

    prompt = "\n\n".join([plan.prompt.text, working_directory_section(layout), "# TASK\ndemo"])
    assert launched.args == (
        "opencode",
        "run",
        "--agent",
        AGENT_NAME,
        "--model",
        "openai/gpt-5",
        prompt,
    )
    assert launched.prompt == prompt


def test_get_env_pins_framework_identity(tmp_path: Path) -> None:
    from ai_hats.env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

    provider = OpenCodeSurface()
    env = provider.get_env(tmp_path / "session", ProjectLayout.at(tmp_path))
    assert env[ENV_AI_HATS_DIR].endswith(".agent/ai-hats") or "ai-hats" in env[ENV_AI_HATS_DIR]
    assert env[AI_HATS_PROJECT_DIR_ENV] == str(tmp_path)


def test_get_run_command_inserts_headless_run_subcommand() -> None:
    provider = OpenCodeSurface()
    command = provider.get_run_command(["opencode", "--agent", AGENT_NAME], "do the task")
    assert command == ["opencode", "run", "--agent", AGENT_NAME, "do the task"]


def test_passthrough_rejects_dangerous_and_owned_flags() -> None:
    provider = OpenCodeSurface()
    assert provider.get_cli_command(["--mini"]) == ["opencode", "--mini"]
    with pytest.raises(ValueError, match="--auto"):
        provider.get_cli_command(["--auto"])
    with pytest.raises(ValueError, match="--agent"):
        provider.get_cli_command(["--agent", "other"])


def test_model_flags_use_opencode_format() -> None:
    provider = OpenCodeSurface()
    assert provider.model_flags("openai/gpt-5") == ["--model", "openai/gpt-5"]
