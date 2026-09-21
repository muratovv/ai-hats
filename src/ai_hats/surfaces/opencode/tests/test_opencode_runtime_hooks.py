"""Runtime-hook planning tests for the OpenCode surface."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces.hook_channel import HookEvent
from ai_hats.surfaces.opencode import OpenCodeSurface
from ai_hats.surfaces.opencode.runtime_hooks import MANIFEST_VERSION, plugin_source
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Executable,
    Hooks,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
)

SESSION_ID = "20260822-000000-1-00000"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def _skill(tmp_path: Path, name: str) -> Skill:
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True)
    document = f"---\nname: {name}\ndescription: Guard skill\n---\n# {name}\n"
    (source / "SKILL.md").write_text(document)
    return Skill(
        name=f"skills::{name}",
        path=source.resolve(),
        content_digest=dir_digest(source),
        document=document,
    )


def _guard(skill: Skill) -> RuntimeHook:
    script = skill.path / "hooks" / "guard.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 2\n")
    script.chmod(0o700)
    run = Executable(path=script, content_digest=hashlib.sha256(script.read_bytes()).hexdigest())
    return RuntimeHook(at=HookEvent.PRE_TOOL_USE, matcher="Bash|run_command", run=run)


def _composition(*skills: Skill, runtime: tuple[RuntimeHook, ...] = ()) -> CompositionPlan:
    return CompositionPlan(
        identity="maintainer",
        prompt=Prompt((PromptBlock(None, (PromptMember("test::prompt", "role", None),)),)),
        skills=skills,
        hooks=Hooks(runtime=runtime, external=()),
        trace=(),
    )


def _plan(tmp_path: Path, composition: CompositionPlan, *, policy=SessionPolicy()):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    layout = ProjectLayout.at(project)
    surface = OpenCodeSurface()
    return layout, surface.plan(
        composition,
        run_mode=RunMode.HITL,
        policy=policy,
        root=layout.cache.session(SESSION_ID),
        layout=layout,
        host=probe_host(surface=surface),
    )


def _entry(plan, name: str):
    return next(e for e in plan.entries if e.target.name == name)


def _config(plan) -> dict:
    entry = next(e for e in plan.entries if e.kind is WriteKind.MERGE_JSON)
    return json.loads(entry.bytes or b"{}")


def test_hooked_composition_registers_plugin_and_manifest(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "safety-guard")
    layout, plan = _plan(tmp_path, _composition(skill, runtime=(_guard(skill),)))

    root = layout.cache.session(SESSION_ID)
    manifest_entry = _entry(plan, "hooks.json")
    plugin_entry = _entry(plan, "ai-hats-hooks.mjs")
    assert manifest_entry.target == root / "opencode" / "hooks.json"
    assert plugin_entry.target == root / "opencode" / "plugin" / "ai-hats-hooks.mjs"
    assert plugin_entry.content == plugin_source()

    manifest = json.loads(manifest_entry.content or "")
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["session"] == {"id": SESSION_ID, "ai_hats_dir": str(layout.base)}
    [pre] = manifest["hooks"]["PreToolUse"]
    assert pre["matcher"] == "Bash|run_command"
    assert pre["command"].endswith("safety-guard/hooks/guard.sh")
    assert pre["tag"] == "ai-hats:safety-guard:PreToolUse:Bash|run_command:guard"
    assert manifest["permissions"] == [
        {"permission": "external_directory", "prefix": f"{layout.cache.root}/", "action": "allow"}
    ]

    assert _config(plan)["plugin"] == [f"file://{plugin_entry.target}"]
    assert plan.env["AI_HATS_SESSION_CACHE_DIR"] == str(root)
    assert plan.env["AI_HATS_PYTHON"] == str(probe_host().python)
    assert plan.env["AI_HATS_HOOK_SURFACE_TIMEOUT_MS"].isdigit()


def test_the_manifest_command_points_into_the_mirror_the_plan_writes(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "safety-guard")
    _layout, plan = _plan(tmp_path, _composition(skill, runtime=(_guard(skill),)))

    mirror = next(
        e for e in plan.entries if e.kind is WriteKind.COPY_TREE and e.source == skill.path
    )
    [pre] = json.loads(_entry(plan, "hooks.json").content or "")["hooks"]["PreToolUse"]
    assert pre["command"] == str(mirror.target / "hooks" / "guard.sh")


def test_hookless_composition_still_ships_permission_rules(tmp_path: Path) -> None:
    """The manifest carries role permission policy even without hooks."""
    layout, plan = _plan(tmp_path, _composition(_skill(tmp_path, "hatrack")))

    manifest = json.loads(_entry(plan, "hooks.json").content or "")
    assert manifest["hooks"] == {}
    assert manifest["permissions"] == [
        {"permission": "external_directory", "prefix": f"{layout.cache.root}/", "action": "allow"}
    ]
    assert _config(plan).get("plugin"), "permission dispatcher must stay registered"


def test_hooks_off_leaves_the_config_without_a_dispatcher(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "safety-guard")
    _layout, plan = _plan(
        tmp_path,
        _composition(skill, runtime=(_guard(skill),)),
        policy=SessionPolicy(hooks=False),
    )

    assert not [e for e in plan.entries if e.target.name in ("hooks.json", "ai-hats-hooks.mjs")]
    assert "plugin" not in _config(plan)
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env


def test_a_hook_outside_every_composed_skill_refuses_the_plan(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "safety-guard")
    stray = _guard(_skill(tmp_path, "not-composed"))

    with pytest.raises(ValueError, match="outside every composed skill"):
        _plan(tmp_path, _composition(skill, runtime=(stray,)))


def test_plugin_asset_is_fail_open_on_missing_pin_and_maps_tools() -> None:
    source = plugin_source()

    assert "process.env.AI_HATS_SESSION_CACHE_DIR" in source
    assert "hookless composition" in source, "missing pin must be an inert no-op, not an error"
    assert "version !== MANIFEST_VERSION" in source


def test_plugin_asset_answers_permission_asks_through_server_api() -> None:
    """Decisions ride the bus event + server reply, rules may defer."""
    source = plugin_source()

    assert '"permission.asked"' in source
    assert "postSessionIdPermissionsPermissionId" in source
    assert 'action === "allow" ? "once" : "reject"' in source
    assert "if (!rule) return;" in source, "unruled asks defer to the platform channel"
