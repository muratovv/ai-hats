"""ClaudeSurface runtime-hook wiring in the per-session settings.json.

Since HATS-1268 every entry is skill-declared and its command is absolute into
the session's own skill mirror; the project root is never written (HATS-1170),
and the user-global leak detector still keys on the pre-1268 spelling because
that is the shape the residue it hunts was written with. The wiring and the
manifest are read off the plan (ADR-0036 D2): nothing here applies anything.
"""

from ai_hats_core.layout import ProjectLayout

import json
import os
from pathlib import Path

import pytest


from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.materialization import WriteKind
from ai_hats.paths import claude_dir
from ai_hats.session_artifacts import RunMode
from ai_hats.session_plan import launch_env
from ai_hats.surfaces.claude.channel import (
    DISPATCHER_COMMAND,
    DISPATCHER_TAG,
    HOOK_NOTIFICATION,
    OBSERVED_NOTIFICATION,
)
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.surfaces.plan import MaterializationPlan, apply
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE
from tests._plan_helpers import composition_of, flags, planned


SETTINGS = Path(".claude") / "settings.json"
SESSION_ID = "test-session-id"


def _plan(project: Path, result: CompositionResult | None = None, run_mode=RunMode.HITL):
    layout = ProjectLayout.at(project)
    res = result or _result([])
    return planned(
        ClaudeSurface(),
        composition_of(res, layout=layout),
        layout=layout,
        root=layout.cache.session(SESSION_ID),
        run_mode=run_mode,
    )


def _written(plan: MaterializationPlan, name: str) -> dict:
    """The JSON document the plan writes under ``name`` — off the entry, not the disk."""
    entry = next(
        e for e in plan.entries if e.kind is WriteKind.WRITE_TEXT and e.target.name == name
    )
    return json.loads(entry.content)


def _settings(project: Path, result: CompositionResult | None = None) -> dict:
    return _written(_plan(project, result), "settings.json")


def _manifest(project: Path, result: CompositionResult | None = None) -> dict:
    return _written(_plan(project, result), "hooks.json")


def _skill_with_runtime_hooks(
    base: Path, name: str, hooks: dict[str, list[tuple[str, str]]]
) -> ResolvedComponent:
    """Skill dir whose SKILL.md frontmatter declares runtime_hooks under
    top-level ``ai_hats:`` (HATS-814) + materializes the hook scripts.

    ``hooks`` maps event -> list of (matcher, script_relpath).
    """
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", "ai_hats:", "  runtime_hooks:"]
    for event, rows in hooks.items():
        lines.append(f"    {event}:")
        for matcher, script in rows:
            lines.append(f"      - matcher: {matcher}")
            lines.append(f"        script: {script}")
            sp = skill_dir / script
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text("#!/usr/bin/env bash\nexit 0\n")
            sp.chmod(0o755)  # a declared gate ships executable, as the library guard demands
    lines += ["---", f"# {name}"]
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n")
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=skills,
        injections=[],
    )


def _managed_command(project: Path, skill: str, script: str) -> str:
    """HATS-1268: absolute, into the session's own skill mirror.

    Spelled out here rather than imported, so the test fails if the emitted
    contract drifts — the same reason the old $CLAUDE_PROJECT_DIR literal was.
    """
    return str(
        ProjectLayout.at(project).cache.session(SESSION_ID) / "plugin" / "skills" / skill / script
    )


OBSERVER_ENTRY = {
    "matcher": OBSERVED_NOTIFICATION,
    "_ai_hats_managed": f"{DISPATCHER_TAG}:{HOOK_NOTIFICATION}",
    "hooks": [{"type": "command", "command": DISPATCHER_COMMAND}],
}


def test_a_composition_without_skills_wires_only_the_observer(tmp_path: Path) -> None:
    """HATS-1268: every GATE is skill-declared, the shared-state guard included
    — an unconditional guard entry once meant settings.json was never empty.
    The one entry that rides no skill judges nothing: it lets the session's own
    record say when claude is showing the person a permission prompt, and it
    runs only then."""
    assert _settings(tmp_path)["hooks"] == {HOOK_NOTIFICATION: [OBSERVER_ENTRY]}


def test_command_is_absolute_into_the_session_skill_mirror(tmp_path: Path) -> None:
    """HATS-615 asked for cwd-independence; an absolute path delivers it.

    The mirror is out of tree, so $CLAUDE_PROJECT_DIR cannot address it.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    cmd = _manifest(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE][0]["command"]
    assert Path(cmd).is_absolute()
    assert "$CLAUDE_PROJECT_DIR" not in cmd
    assert cmd == _managed_command(proj, "skill-x", "hooks/pre.sh")


def test_a_leaked_ai_hats_dir_cannot_redirect_the_command(tmp_path: Path, monkeypatch) -> None:
    """What retired the out-of-tree warning (HATS-1268).

    Commands used to be built from AI_HATS_DIR, so a leaked env var moved them
    and only a warning stood between that and a foreign project's scripts. They
    are built from the session cache root now, which is keyed on the project
    itself (HATS-897), so the redirect is not expressible.
    """
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(tmp_path / "elsewhere" / "ai-hats"))
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    cmd = _manifest(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE][0]["command"]
    assert "elsewhere" not in cmd
    assert cmd.startswith(str(ProjectLayout.at(proj).cache.session(SESSION_ID)))


def test_foreign_session_pair_does_not_cross_write_settings(tmp_path: Path, monkeypatch) -> None:
    dev_repo = tmp_path / "dev-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(dev_repo / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(dev_repo))
    victim = tmp_path / "victim"
    (victim / ".agent").mkdir(parents=True)
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    cmd = _manifest(victim, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE][0]["command"]
    assert cmd == _managed_command(victim, "skill-x", "hooks/pre.sh")
    assert str(dev_repo) not in cmd


# ----- HATS-597: skill-declared runtime hooks -----


def test_each_bound_event_gets_one_dispatcher_entry(tmp_path: Path) -> None:
    """HATS-1874: the harness delivers the call, the dispatcher decides which
    gates it matched. Which gates those are lives in the manifest — asserted by
    test_the_manifest_names_exactly_the_composed_gates."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {
            HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")],
            HOOK_POST_TOOL_USE: [("Edit|Write", "hooks/post.sh")],
        },
    )
    hooks = _settings(proj, _result([skill]))["hooks"]

    assert set(hooks) == {HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE, HOOK_NOTIFICATION}
    for event, matcher in ((HOOK_PRE_TOOL_USE, "Bash"), (HOOK_POST_TOOL_USE, "Edit|Write")):
        assert hooks[event] == [
            {
                "matcher": matcher,
                "_ai_hats_managed": f"{DISPATCHER_TAG}:{event}",
                "hooks": [{"type": "command", "command": DISPATCHER_COMMAND}],
            }
        ]


def test_an_event_no_gate_binds_to_gets_no_entry(tmp_path: Path) -> None:
    """A dispatcher spawned to find nothing is ~40 ms of nothing, per call.
    The observer is not that: it binds one notification type, so it runs only
    when the person is being asked — and is waiting anyway."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )

    hooks = _settings(proj, _result([skill]))["hooks"]
    assert set(hooks) == {HOOK_PRE_TOOL_USE, HOOK_NOTIFICATION}
    assert hooks[HOOK_NOTIFICATION] == [OBSERVER_ENTRY]


def test_claude_skill_hooks_idempotent(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    first = _plan(proj, _result([skill]))
    apply(first)
    assert _plan(proj, _result([skill])) == first
    assert _settings(proj, _result([skill])) == _written(first, "settings.json")


def test_a_skill_that_left_the_composition_leaves_no_entry_and_the_root_untouched(
    tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    claude_dir(proj).mkdir(parents=True)
    # A user-authored project entry is never read or written by a session.
    user_settings = json.dumps(
        {
            "hooks": {
                HOOK_POST_TOOL_USE: [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "user/p.sh"}]}
                ]
            }
        }
    )
    (proj / SETTINGS).write_text(user_settings)
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {
            HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")],
            HOOK_POST_TOOL_USE: [("Write", "hooks/post.sh")],
        },
    )
    apply(_plan(proj, _result([skill])))
    # Skill leaves the composition → the next session plans without it.
    later = _plan(proj, _result([]))
    apply(later)
    data = _written(later, "settings.json")

    tags = [
        e.get("_ai_hats_managed")
        for entries in data["hooks"].values()
        if isinstance(entries, list)
        for e in entries
    ]
    # All skill-x managed entries gone.
    assert not any(t and t.startswith("ai-hats:skill-x") for t in tags)
    assert (proj / SETTINGS).read_text() == user_settings


def test_claude_two_matchers_same_event_no_tag_collision(tmp_path: Path) -> None:
    """Fix #4: two hooks for the same (skill, event) but different matchers
    yield two distinct managed entries — the matcher is part of the tag."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {HOOK_PRE_TOOL_USE: [("Bash", "hooks/a.sh"), ("Edit", "hooks/b.sh")]},
    )
    skill_tags = {
        row["tag"] for row in _manifest(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE]
    }
    assert skill_tags == {
        "ai-hats:skill-x:PreToolUse:Bash:a",
        "ai-hats:skill-x:PreToolUse:Edit:b",
    }


# ----- HATS-961: leaked user-global project-hook detector -----

# The leak as the incident had it — the pre-HATS-1268 spelling on purpose:
# what this detector finds is old wiring left in a user's global settings, and
# that residue keeps the shape it was written with.
LEAKED_GUARD = "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"


def _seed_global_leak(home: Path, extra: list[dict] | None = None) -> Path:
    """Seed <home>/.claude/settings.json with a leaked ai-hats project hook."""
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "matcher": "Bash",
            "_ai_hats_managed": "ai-hats:hats-437",
            "hooks": [{"type": "command", "command": LEAKED_GUARD}],
        }
    ]
    if extra:
        entries.extend(extra)
    settings.write_text(json.dumps({"hooks": {HOOK_PRE_TOOL_USE: entries}}))
    return settings


def test_leak_detector_returns_tagged_and_untagged(tmp_path: Path) -> None:
    """Both a tagged ($CLAUDE_PROJECT_DIR) and an untagged bare-relative leak are
    caught — detection is by command substring, not the ``_ai_hats_managed`` tag."""
    home = tmp_path / "home"
    untagged = ".agent/ai-hats/library/hooks/tool-call-hygiene-posttooluse.sh"
    _seed_global_leak(
        home,
        extra=[{"matcher": "Write", "hooks": [{"type": "command", "command": untagged}]}],
    )
    assert ClaudeSurface().leaked_user_global_project_hooks(home) == [LEAKED_GUARD, untagged]


def test_leak_detector_does_not_mutate_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = _seed_global_leak(home)
    before = settings.read_text()
    ClaudeSurface().leaked_user_global_project_hooks(home)
    assert settings.read_text() == before


def test_leak_detector_clean_when_only_user_hooks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "~/mine.sh"}]}
                    ]
                }
            }
        )
    )
    assert ClaudeSurface().leaked_user_global_project_hooks(home) == []


def test_leak_detector_empty_when_no_file(tmp_path: Path) -> None:
    assert ClaudeSurface().leaked_user_global_project_hooks(tmp_path / "home") == []


def test_leak_detector_tolerates_malformed_json(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not valid json ,,,")
    assert ClaudeSurface().leaked_user_global_project_hooks(home) == []


def test_leak_detector_tolerates_binary(tmp_path: Path) -> None:
    """Non-UTF8 file → [] (UnicodeDecodeError is a ValueError) — never crashes update."""
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert ClaudeSurface().leaked_user_global_project_hooks(home) == []


def test_leak_detector_empty_on_non_object_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text("[]")
    assert ClaudeSurface().leaked_user_global_project_hooks(home) == []


def test_base_surface_reports_no_leaks(tmp_path: Path) -> None:
    """Agy (base default) manages no user-global hooks → [] even with a seeded
    Claude leak. Each surface owns its own detection (HATS-961)."""
    home = tmp_path / "home"
    _seed_global_leak(home)
    assert AgySurface().leaked_user_global_project_hooks(home) == []


def test_leak_detector_catches_session_tree_leaked_hooks(tmp_path: Path) -> None:
    """R2 #1, D1 (HATS-1480): Leak detector catches hook commands pointing at
    session tree plugin/skills/<skill>/hooks/ paths."""
    home = tmp_path / "home"
    leaked_cmd = "$CLAUDE_PROJECT_DIR/.agent/ai-hats/sessions/20260804-123456/plugin/skills/safety-guard/hooks/wt_gate.py"
    _seed_global_leak(
        home,
        extra=[
            {
                "matcher": "Edit",
                "_ai_hats_managed": "ai-hats:safety-guard",
                "hooks": [{"type": "command", "command": leaked_cmd}],
            }
        ],
    )
    res = ClaudeSurface().leaked_user_global_project_hooks(home)
    assert leaked_cmd in res


def _hook_skills(base: Path) -> list[ResolvedComponent]:
    return [
        _skill_with_runtime_hooks(base, "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}),
        _skill_with_runtime_hooks(
            base, "skill-y", {HOOK_POST_TOOL_USE: [("Edit|Write", "hooks/post.sh")]}
        ),
    ]


def test_the_manifest_names_exactly_the_composed_gates(tmp_path: Path) -> None:
    """HATS-1874: what the dispatcher reads, spelled out rather than imported —
    a manifest that names a gate the composition does not is a gate nobody
    declared, and one it omits is a gate that stops running."""
    proj = tmp_path / "proj"
    proj.mkdir()

    manifest = _manifest(proj, _result(_hook_skills(tmp_path / "skills")))["hooks"]

    assert manifest == {
        HOOK_PRE_TOOL_USE: [
            {
                "matcher": "Bash",
                "command": _managed_command(proj, "skill-x", "hooks/pre.sh"),
                "tag": f"ai-hats:skill-x:{HOOK_PRE_TOOL_USE}:Bash:pre",
            }
        ],
        HOOK_POST_TOOL_USE: [
            {
                "matcher": "Edit|Write",
                "command": _managed_command(proj, "skill-y", "hooks/post.sh"),
                "tag": f"ai-hats:skill-y:{HOOK_POST_TOOL_USE}:Edit|Write:post",
            }
        ],
    }


def test_a_gate_whose_script_is_gone_is_missing_from_both_and_said_so(tmp_path: Path) -> None:
    """A script the skill no longer ships is the author's to fix: the gate is
    left out of the wiring AND the manifest alike, and the session is told —
    once, by the adapter that read the library, for every projection alike."""
    from ai_hats.resolver import LibraryResolver
    from ai_hats.surfaces import adapt

    proj = tmp_path / "proj"
    proj.mkdir()
    layout = ProjectLayout.at(proj)
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    (skill.source_path / "hooks" / "pre.sh").unlink()
    diagnostics: list = []
    composition = adapt(
        _result([skill]),
        identity="r",
        layout=layout,
        resolver=LibraryResolver([]),
        overlays=(),
        diagnostics=diagnostics,
    )
    plan = planned(
        ClaudeSurface(), composition, layout=layout, root=layout.cache.session(SESSION_ID)
    )

    assert _written(plan, "settings.json")["hooks"] == {HOOK_NOTIFICATION: [OBSERVER_ENTRY]}
    assert _written(plan, "hooks.json")["hooks"] == {}
    [notice] = [d.text for d in diagnostics if "hooks/pre.sh" in d.text]
    assert "skill-x" in notice and "will not run" in notice


def test_the_mirror_the_manifest_points_into_is_a_tree_the_plan_writes(tmp_path: Path) -> None:
    """The mirror is executable by construction (ADR-0036 D2): every command
    lies under a ``copy_tree`` entry of the same plan, and application keeps
    the mode the library shipped — so no build-time check of the mirror is
    needed, and none is made."""
    proj = tmp_path / "proj"
    proj.mkdir()
    plan = _plan(proj, _result(_hook_skills(tmp_path / "skills")))
    trees = [e.target for e in plan.entries if e.kind is WriteKind.COPY_TREE]

    commands = [
        Path(row["command"])
        for rows in _written(plan, "hooks.json")["hooks"].values()
        for row in rows
    ]
    assert commands and all(any(c.is_relative_to(t) for t in trees) for c in commands)
    apply(plan)
    assert all(os.access(c, os.X_OK) for c in commands)


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_pins_the_dispatcher_needs_reach_the_launch(tmp_path: Path, run_mode) -> None:
    """The dispatcher's settings entry is a shell guard, and without these two
    it refuses — a refusal AI_HATS_GATE_BROKEN_ACK cannot open, because the
    shell never reaches the python that honours it."""
    proj = tmp_path / "proj"
    proj.mkdir()
    layout = ProjectLayout.at(proj)
    plan = _plan(proj, _result(_hook_skills(tmp_path / "skills")), run_mode)

    env = launch_env(plan, ClaudeSurface(), flags(plan.root), layout=layout)

    assert env["AI_HATS_SESSION_CACHE_DIR"] == str(layout.cache.session(SESSION_ID))
    assert Path(env["AI_HATS_PYTHON"]).exists()


def test_the_entry_matcher_is_the_union_of_its_rows(tmp_path: Path) -> None:
    """Under `*` a dispatcher spawns on every Read and Grep to find that nothing
    matched — ~45 ms of nothing, where the harness spawns nothing at all."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {HOOK_PRE_TOOL_USE: [("Bash|execute", "hooks/a.sh"), ("Bash", "hooks/b.sh")]},
    )

    entries = _settings(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE]

    assert entries[0]["matcher"] == "Bash|execute", "duplicates repeated, or the union lost a name"


def test_a_gate_bound_to_everything_keeps_the_entry_open(tmp_path: Path) -> None:
    """The union may only ever widen: a row nothing narrows must not be narrowed
    by the entry above it, or that gate stops running."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {HOOK_PRE_TOOL_USE: [('"*"', "hooks/a.sh"), ("Bash", "hooks/b.sh")]},
    )

    entries = _settings(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE]

    assert entries[0]["matcher"] == "*"
