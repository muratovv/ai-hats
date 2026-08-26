"""ClaudeSurface runtime-hook wiring in the per-session settings.json.

Since HATS-1268 every entry is skill-declared and its command is absolute into
the session's own skill mirror; the project root is never written (HATS-1170),
and the user-global leak detector still keys on the pre-1268 spelling because
that is the shape the residue it hunts was written with.
"""

import json
from pathlib import Path


from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.paths import claude_dir, session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE


SETTINGS = Path(".claude") / "settings.json"
SESSION_ID = "test-session-id"


def _settings(project: Path, result: CompositionResult | None = None) -> dict:
    provider = ClaudeSurface()
    res = result or _result([])
    artifacts = provider.build_session_artifacts(
        project, res, "test-session-id", run_mode="hitl", artifacts=BuiltArtifacts()
    )
    settings_file = [
        Path(artifacts.cli_args[i + 1])
        for i, arg in enumerate(artifacts.cli_args)
        if arg == "--settings"
    ][0]
    return json.loads(settings_file.read_text())


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
    return str(session_cache_dir(project, SESSION_ID) / "plugin" / "skills" / skill / script)


def test_a_composition_without_skills_wires_nothing(tmp_path: Path) -> None:
    """HATS-1268: every entry is skill-declared, the shared-state guard included.

    Before, an unconditional guard entry meant settings.json was never empty.
    """
    assert _settings(tmp_path)["hooks"] == {}


def test_command_is_absolute_into_the_session_skill_mirror(tmp_path: Path) -> None:
    """HATS-615 asked for cwd-independence; an absolute path delivers it.

    The mirror is out of tree, so $CLAUDE_PROJECT_DIR cannot address it.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    cmd = _settings(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
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
    cmd = _settings(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
    assert "elsewhere" not in cmd
    assert cmd.startswith(str(session_cache_dir(proj, SESSION_ID)))


def test_foreign_session_pair_does_not_cross_write_settings(tmp_path: Path, monkeypatch) -> None:
    dev_repo = tmp_path / "dev-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(dev_repo / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(dev_repo))
    victim = tmp_path / "victim"
    (victim / ".agent").mkdir(parents=True)
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    data = _settings(victim, _result([skill]))
    cmd = data["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
    assert cmd == _managed_command(victim, "skill-x", "hooks/pre.sh")
    assert str(dev_repo) not in cmd


def test_claude_ensure_runtime_hooks_leaves_root_clean(tmp_path: Path) -> None:
    """HATS-1170: ensure_runtime_hooks is a no-op for project-root .claude/settings.json."""
    ClaudeSurface().ensure_runtime_hooks(tmp_path)
    assert not (tmp_path / SETTINGS).exists()


def test_agy_provider_does_not_touch_settings(tmp_path: Path) -> None:
    AgySurface().ensure_runtime_hooks(tmp_path)
    assert not (tmp_path / SETTINGS).exists()


# ----- HATS-597: skill-declared runtime hooks -----


def test_claude_wires_skill_runtime_hooks_under_each_event(tmp_path: Path) -> None:
    """One managed entry per (event, skill, matcher), tagged distinctly."""
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
    ClaudeSurface().ensure_runtime_hooks(proj, _result([skill]))
    data = _settings(proj, _result([skill]))

    # Skill PreToolUse entry.
    pre = data["hooks"][HOOK_PRE_TOOL_USE]
    sp = [e for e in pre if e.get("_ai_hats_managed") == "ai-hats:skill-x:PreToolUse:Bash"]
    assert len(sp) == 1
    assert sp[0]["matcher"] == "Bash"
    assert sp[0]["hooks"] == [
        {"type": "command", "command": _managed_command(proj, "skill-x", "hooks/pre.sh")}
    ]

    # Skill PostToolUse entry under the PostToolUse event.
    post = data["hooks"][HOOK_POST_TOOL_USE]
    pe = [e for e in post if e.get("_ai_hats_managed") == "ai-hats:skill-x:PostToolUse:Edit|Write"]
    assert len(pe) == 1
    assert pe[0]["matcher"] == "Edit|Write"
    assert pe[0]["hooks"] == [
        {"type": "command", "command": _managed_command(proj, "skill-x", "hooks/post.sh")}
    ]


def test_claude_skill_hooks_idempotent(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills", "skill-x", {HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")]}
    )
    ClaudeSurface().ensure_runtime_hooks(proj, _result([skill]))
    first = _settings(proj, _result([skill]))
    ClaudeSurface().ensure_runtime_hooks(proj, _result([skill]))
    assert _settings(proj, _result([skill])) == first


def test_claude_removing_skill_sweeps_entries_and_keeps_user(
    tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    claude_dir(proj).mkdir(parents=True)
    # A user-authored PostToolUse entry must survive the sweep.
    (proj / SETTINGS).write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_POST_TOOL_USE: [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "user/p.sh"}]}
                    ]
                }
            }
        )
    )
    skill = _skill_with_runtime_hooks(
        tmp_path / "skills",
        "skill-x",
        {
            HOOK_PRE_TOOL_USE: [("Bash", "hooks/pre.sh")],
            HOOK_POST_TOOL_USE: [("Write", "hooks/post.sh")],
        },
    )
    ClaudeSurface().ensure_runtime_hooks(proj, _result([skill]))
    # Skill leaves the composition → re-apply with no skills.
    ClaudeSurface().ensure_runtime_hooks(proj, _result([]))
    data = _settings(proj, _result([]))

    tags = [
        e.get("_ai_hats_managed")
        for entries in data["hooks"].values()
        if isinstance(entries, list)
        for e in entries
    ]
    # All skill-x managed entries swept.
    assert not any(t and t.startswith("ai-hats:skill-x") for t in tags)


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
    ClaudeSurface().ensure_runtime_hooks(proj, _result([skill]))
    pre = _settings(proj, _result([skill]))["hooks"][HOOK_PRE_TOOL_USE]
    skill_tags = {
        e["_ai_hats_managed"]
        for e in pre
        if e.get("_ai_hats_managed", "").startswith("ai-hats:skill-x")
    }
    assert skill_tags == {
        "ai-hats:skill-x:PreToolUse:Bash",
        "ai-hats:skill-x:PreToolUse:Edit",
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
