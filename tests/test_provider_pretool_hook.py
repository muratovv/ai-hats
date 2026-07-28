"""HATS-437 — ClaudeProvider.ensure_runtime_hooks PreToolUse autowire.

Covers:
    - fresh write into .claude/settings.json
    - idempotency on double-apply
    - preservation of pre-existing user-authored PreToolUse entries
    - skip when user already wired the same hook manually
    - update-in-place when the managed entry changes (e.g. hook path moved)
    - Agy provider is a no-op (does not touch settings.json)
    - malformed / non-object JSON: leave alone (no clobber)
"""

import json
from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.paths import claude_dir, hooks_dir, managed_runtime_hook_filename
from ai_hats.session_artifacts import BuiltArtifacts
from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats_agy.provider import AgyProvider
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE


SETTINGS = Path(".claude") / "settings.json"
# HATS-615: managed hook commands are emitted with a literal
# $CLAUDE_PROJECT_DIR/ prefix (Claude Code expands it at hook-execution time)
# so they resolve regardless of the agent cwd. Hard-coded literal here — NOT
# imported from paths.CLAUDE_PROJECT_DIR_VAR — so this test fails if the
# emitted contract drifts from the documented placeholder.
PREFIX = "$CLAUDE_PROJECT_DIR/"
EXPECTED_REL = PREFIX + ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"


def _settings(project: Path, result: CompositionResult | None = None) -> dict:
    provider = ClaudeProvider()
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
    return PREFIX + str(
        (hooks_dir(project) / managed_runtime_hook_filename(skill, script)).relative_to(project)
    )


def test_claude_writes_fresh_settings(tmp_path: Path) -> None:
    data = _settings(tmp_path)
    entries = data["hooks"][HOOK_PRE_TOOL_USE]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["matcher"] == "Bash"
    assert entry["_ai_hats_managed"] == "ai-hats:hats-437"
    assert entry["hooks"] == [{"type": "command", "command": EXPECTED_REL}]


def test_guard_command_uses_claude_project_dir_prefix(tmp_path: Path) -> None:
    cmd = _settings(tmp_path)["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
    assert cmd.startswith("$CLAUDE_PROJECT_DIR/")
    assert cmd.endswith("/pre_bash_shared_state_guard.sh")


def test_out_of_project_hook_paths_warn_loudly(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "elsewhere" / "ai-hats"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(base))
    project = tmp_path / "project"
    project.mkdir()
    with pytest.warns(UserWarning, match="outside the project"):
        data = _settings(project)
    cmd = data["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
    assert cmd == str(base / "library" / "hooks" / "pre_bash_shared_state_guard.sh")


def test_in_project_hook_paths_do_not_warn(tmp_path: Path, recwarn) -> None:
    _settings(tmp_path)
    assert not [w for w in recwarn if "outside the project" in str(w.message)]


def test_foreign_session_pair_does_not_cross_write_settings(tmp_path: Path, monkeypatch) -> None:
    dev_repo = tmp_path / "dev-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(dev_repo / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(dev_repo))
    victim = tmp_path / "victim"
    (victim / ".agent").mkdir(parents=True)
    with pytest.warns(UserWarning, match=ENV_AI_HATS_DIR):
        data = _settings(victim)
    cmd = data["hooks"][HOOK_PRE_TOOL_USE][0]["hooks"][0]["command"]
    assert cmd == EXPECTED_REL


def test_claude_ensure_runtime_hooks_leaves_root_clean(tmp_path: Path) -> None:
    """HATS-1170: ensure_runtime_hooks is a no-op for project-root .claude/settings.json."""
    ClaudeProvider().ensure_runtime_hooks(tmp_path)
    assert not (tmp_path / SETTINGS).exists()


def test_agy_provider_does_not_touch_settings(tmp_path: Path) -> None:
    AgyProvider().ensure_runtime_hooks(tmp_path)
    assert not (tmp_path / SETTINGS).exists()


# ----- HATS-597: skill-declared runtime hooks -----


def test_claude_wires_skill_runtime_hooks_under_each_event(tmp_path: Path) -> None:
    """A skill declaring PreToolUse + PostToolUse hooks gets one managed entry
    per (event, skill, matcher), tagged distinctly, alongside the hats-437
    guard."""
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
    ClaudeProvider().ensure_runtime_hooks(proj, _result([skill]))
    data = _settings(proj, _result([skill]))

    # hats-437 guard still present under PreToolUse.
    pre = data["hooks"][HOOK_PRE_TOOL_USE]
    guard = [e for e in pre if e.get("_ai_hats_managed") == "ai-hats:hats-437"]
    assert len(guard) == 1

    # Skill PreToolUse entry.
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
    ClaudeProvider().ensure_runtime_hooks(proj, _result([skill]))
    first = _settings(proj, _result([skill]))
    ClaudeProvider().ensure_runtime_hooks(proj, _result([skill]))
    assert _settings(proj, _result([skill])) == first


def test_claude_removing_skill_sweeps_entries_keeps_guard_and_user(
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
    ClaudeProvider().ensure_runtime_hooks(proj, _result([skill]))
    # Skill leaves the composition → re-apply with no skills.
    ClaudeProvider().ensure_runtime_hooks(proj, _result([]))
    data = _settings(proj, _result([]))

    tags = [
        e.get("_ai_hats_managed")
        for entries in data["hooks"].values()
        if isinstance(entries, list)
        for e in entries
    ]
    # All skill-x managed entries swept; guard survives.
    assert "ai-hats:hats-437" in tags
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
    ClaudeProvider().ensure_runtime_hooks(proj, _result([skill]))
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

# A tagged guard leak ($CLAUDE_PROJECT_DIR-prefixed) as the incident had it.
LEAKED_GUARD = PREFIX + ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"


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
    assert ClaudeProvider().leaked_user_global_project_hooks(home) == [LEAKED_GUARD, untagged]


def test_leak_detector_does_not_mutate_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = _seed_global_leak(home)
    before = settings.read_text()
    ClaudeProvider().leaked_user_global_project_hooks(home)
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
    assert ClaudeProvider().leaked_user_global_project_hooks(home) == []


def test_leak_detector_empty_when_no_file(tmp_path: Path) -> None:
    assert ClaudeProvider().leaked_user_global_project_hooks(tmp_path / "home") == []


def test_leak_detector_tolerates_malformed_json(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not valid json ,,,")
    assert ClaudeProvider().leaked_user_global_project_hooks(home) == []


def test_leak_detector_tolerates_binary(tmp_path: Path) -> None:
    """Non-UTF8 file → [] (UnicodeDecodeError is a ValueError) — never crashes update."""
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert ClaudeProvider().leaked_user_global_project_hooks(home) == []


def test_leak_detector_empty_on_non_object_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = home / SETTINGS
    settings.parent.mkdir(parents=True)
    settings.write_text("[]")
    assert ClaudeProvider().leaked_user_global_project_hooks(home) == []


def test_base_surface_reports_no_leaks(tmp_path: Path) -> None:
    """Agy (base default) manages no user-global hooks → [] even with a seeded
    Claude leak. Each surface owns its own detection (HATS-961)."""
    home = tmp_path / "home"
    _seed_global_leak(home)
    assert AgyProvider().leaked_user_global_project_hooks(home) == []
