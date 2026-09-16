"""claude plans a session from the composition half alone (ADR-0036 D2)."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.constants import INJECTION_END, INJECTION_START
from ai_hats.env import (
    ENV_AI_HATS_PYTHON,
    ENV_HOOK_SOCKET,
    ENV_SESSION_CACHE_DIR,
    ENV_STATUSLINE_INNER,
)
from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.surfaces import HookEvent
from ai_hats.surfaces.claude.channel import DISPATCHER_COMMAND, DISPATCHER_TAG
from ai_hats.surfaces.claude.hook_server import socket_path
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.claude.statusline import STATUSLINE_COMMAND
from ai_hats.surfaces.hook_dispatch import MANIFEST_VERSION
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Executable,
    Hooks,
    Host,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
)

HOST = Host(python=Path("/opt/py/bin/python3"), path="/usr/bin", commands={})


def _composition(skills=(), hooks=Hooks((), ())) -> CompositionPlan:
    return CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=tuple(skills),
        hooks=hooks,
        trace=(),
    )


def _plan(tmp_path: Path, composition: CompositionPlan, run_mode=RunMode.HITL, policy=None):
    return ClaudeSurface().plan(
        composition,
        run_mode=run_mode,
        policy=policy or SessionPolicy(),
        root=tmp_path / "sessions" / "s1",
        layout=ProjectLayout.at(tmp_path / "proj"),
        host=HOST,
    )


def _flag(args, name: str) -> str:
    return args[args.index(name) + 1]


# ── context ──────────────────────────────────────────────────────────────────


def test_the_context_entry_is_the_prompt_text_inside_the_markers(tmp_path: Path):
    plan = _plan(tmp_path, _composition())
    context = next(e for e in plan.entries if e.target == plan.root / "prompt.md")
    assert context.kind is WriteKind.WRITE_TEXT
    assert context.content == f"{INJECTION_START}\n{plan.prompt.text}\n{INJECTION_END}\n"
    assert plan.prompt == plan.composition.prompt, "claude adds no block of its own"
    assert _flag(plan.launch.args, "--system-prompt-file") == str(context.target)
    assert plan.launch.sdk_options is None


def test_automate_hands_the_sdk_the_bare_text_appended_to_the_preset(tmp_path: Path):
    plan = _plan(tmp_path, _composition(), run_mode=RunMode.AUTOMATE)
    assert plan.launch.args is None
    assert plan.launch.sdk_options["system_prompt"] == {
        "type": "preset",
        "preset": "claude_code",
        "append": plan.prompt.text,
    }
    assert any(e.target == plan.root / "prompt.md" for e in plan.entries), "audit symmetry"


def test_a_policy_without_context_delivers_no_prompt(tmp_path: Path):
    plan = _plan(tmp_path, _composition(), policy=SessionPolicy(context=False))
    assert not any(e.target == plan.root / "prompt.md" for e in plan.entries)
    assert "--system-prompt-file" not in plan.launch.args


# ── skills ───────────────────────────────────────────────────────────────────


def _skill(tmp_path: Path, name: str, *, document: str | None = "# s\n", on_path=()) -> Skill:
    tree = tmp_path / "lib" / "skills" / name
    tree.mkdir(parents=True)
    return Skill(f"skills::{name}", tree.resolve(), "ab" * 32, document=document, on_path=on_path)


def test_skills_mirror_each_tree_and_rewrite_its_document(tmp_path: Path):
    skill = _skill(tmp_path, "s", document="# rendered\n", on_path=("scripts", "bin"))
    plan = _plan(tmp_path, _composition(skills=[skill]))
    mirror = plan.root / "plugin" / "skills" / "s"
    tree = next(e for e in plan.entries if e.kind is WriteKind.COPY_TREE)
    assert (tree.source, tree.target, tree.tree_digest) == (
        skill.path,
        mirror,
        skill.content_digest,
    )
    document = next(e for e in plan.entries if e.target == mirror / "SKILL.md")
    assert document.content == "# rendered\n"
    assert plan.entries.index(tree) < plan.entries.index(document), "the document shadows the copy"
    manifest = next(e for e in plan.entries if e.target.name == "plugin.json")
    assert json.loads(manifest.content) == {"name": "ai-hats-r", "version": "0.0.0"}
    assert plan.env["PATH"] == os.pathsep.join(
        [str(mirror / "scripts"), str(mirror / "bin"), "/usr/bin"]
    )
    assert _flag(plan.launch.args, "--plugin-dir") == str(plan.root / "plugin")
    assert not any(e.kind is WriteKind.REMOVE_TREE for e in plan.entries), (
        "a wipe is not idempotent"
    )


def test_a_skill_without_a_document_or_scripts_adds_neither(tmp_path: Path):
    plan = _plan(tmp_path, _composition(skills=[_skill(tmp_path, "s", document=None)]))
    assert not any(e.target.name == "SKILL.md" for e in plan.entries)
    assert "PATH" not in plan.env


def test_automate_registers_the_plugin_as_a_local_sdk_plugin(tmp_path: Path):
    plan = _plan(tmp_path, _composition(skills=[_skill(tmp_path, "s")]), run_mode=RunMode.AUTOMATE)
    assert plan.launch.sdk_options["plugins"] == [
        {"type": "local", "path": str(plan.root / "plugin")}
    ]
    assert (
        _plan(tmp_path, _composition(), run_mode=RunMode.AUTOMATE).launch.sdk_options["plugins"]
        == []
    )


def test_the_plugin_name_is_a_slug_of_the_identity(tmp_path: Path):
    plan = _plan(tmp_path, dataclasses.replace(_composition(), identity="maintainer + sre"))
    manifest = next(e for e in plan.entries if e.target.name == "plugin.json")
    assert json.loads(manifest.content)["name"] == "ai-hats-maintainer-sre"


# ── hooks ────────────────────────────────────────────────────────────────────


def _hook(skill: Skill, inside: str) -> RuntimeHook:
    script = skill.path / inside
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\n")
    return RuntimeHook(HookEvent.PRE_TOOL_USE, "Bash", Executable(script.resolve(), "cd" * 32))


def _content(plan, name: str) -> dict:
    return json.loads(next(e for e in plan.entries if e.target == plan.root / name).content)


def test_hooks_are_wired_to_the_mirrored_script_through_the_dispatcher(tmp_path: Path):
    skill = _skill(tmp_path, "guard")
    plan = _plan(tmp_path, _composition([skill], Hooks((_hook(skill, "hooks/gate.py"),), ())))
    mirror = plan.root / "plugin" / "skills" / "guard"
    rows = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "command": str(mirror / "hooks" / "gate.py"),
                "tag": "ai-hats:guard:PreToolUse:Bash:gate",
            }
        ]
    }
    assert _content(plan, "hooks.json") == {
        "version": MANIFEST_VERSION,
        "session": {"id": "s1"},
        "hooks": rows,
    }
    # The wiring is built from the manifest's rows — the same pass, so neither
    # can name a gate the other does not; the entry's shape is the dispatcher's.
    wired = _content(plan, "settings.json")
    assert wired["hooks"] == ClaudeSurface()._desired_runtime_entries(rows)
    entry = wired["hooks"]["PreToolUse"][0]
    assert (
        entry["matcher"] == "Bash" and entry["_ai_hats_managed"] == f"{DISPATCHER_TAG}:PreToolUse"
    )
    assert entry["hooks"][0]["command"] == DISPATCHER_COMMAND
    assert plan.env[ENV_SESSION_CACHE_DIR] == str(plan.root)
    assert plan.env[ENV_AI_HATS_PYTHON] == "/opt/py/bin/python3"
    assert plan.env[ENV_HOOK_SOCKET] == str(socket_path(plan.root))
    assert _flag(plan.launch.args, "--settings") == str(plan.root / "settings.json")


def test_automate_hands_the_settings_to_the_sdk_and_no_other_source(tmp_path: Path):
    plan = _plan(tmp_path, _composition(), run_mode=RunMode.AUTOMATE)
    assert plan.launch.sdk_options["settings"] == str(plan.root / "settings.json")
    assert plan.launch.sdk_options["setting_sources"] == []
    assert _content(plan, "hooks.json")["hooks"] == {}, "an empty manifest is still written"


# ── the status line ─────────────────────────────────────────────────────────


def _person_bar(tmp_path: Path, monkeypatch, entry: dict | None) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if entry is not None:
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(json.dumps({"statusLine": entry}))


def test_hitl_wires_the_status_line_and_hands_the_persons_own_bar_down(tmp_path, monkeypatch):
    """`--settings` replaces the `statusLine` slot, so ours records the quota
    state and then runs theirs — whose command rides the env, and whose
    padding stays on the entry."""
    _person_bar(
        tmp_path, monkeypatch, {"type": "command", "command": "bash ~/bar.sh", "padding": 0}
    )
    plan = _plan(tmp_path, _composition())

    wired = _content(plan, "settings.json")
    assert wired["statusLine"] == {"type": "command", "command": STATUSLINE_COMMAND, "padding": 0}
    assert plan.env[ENV_STATUSLINE_INNER] == "bash ~/bar.sh"


def test_hitl_with_no_bar_of_the_persons_wires_the_recorder_alone(tmp_path, monkeypatch):
    _person_bar(tmp_path, monkeypatch, None)
    plan = _plan(tmp_path, _composition())

    assert _content(plan, "settings.json")["statusLine"] == {
        "type": "command",
        "command": STATUSLINE_COMMAND,
    }
    assert ENV_STATUSLINE_INNER not in plan.env


def test_automate_wires_no_status_line(tmp_path, monkeypatch):
    """The SDK stream carries the quota state itself, and no TUI renders a bar."""
    _person_bar(tmp_path, monkeypatch, {"type": "command", "command": "bash ~/bar.sh"})
    plan = _plan(tmp_path, _composition(), run_mode=RunMode.AUTOMATE)

    assert "statusLine" not in _content(plan, "settings.json")
    assert ENV_STATUSLINE_INNER not in plan.env


def test_a_policy_without_hooks_wires_none(tmp_path: Path):
    plan = _plan(tmp_path, _composition(), policy=SessionPolicy(hooks=False))
    assert not any(e.target.name in ("hooks.json", "settings.json") for e in plan.entries)
    assert "--settings" not in plan.launch.args and ENV_HOOK_SOCKET not in plan.env


def test_a_hook_outside_every_composed_skill_is_refused(tmp_path: Path):
    stray = RuntimeHook(
        HookEvent.PRE_TOOL_USE, "Bash", Executable(Path("/elsewhere/g.py"), "cd" * 32)
    )
    with pytest.raises(ValueError, match="outside every composed skill"):
        _plan(tmp_path, _composition(hooks=Hooks((stray,), ())))
