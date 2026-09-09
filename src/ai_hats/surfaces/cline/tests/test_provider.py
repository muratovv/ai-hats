"""Contract tests for ``ClineSurface`` (HATS-956, HATS-963, HATS-1171).

Pure-method assertions (no real cline, no auth): the CLI-shape, env, inline
`-s` role delivery, and the per-session-cache skill materialization the ai-hats
runners depend on. HATS-1171: cline runs through the unified artifact-builder
(ADR-0018) on the clean-root invariant — skills land in
``<cache_root>/sessions/<sid>/skills`` (delivered via ``--config``),
never in the project root; the dead TS hook plugin is gone.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.session_artifacts import BuiltArtifacts, RunMode, SessionPolicy
from ai_hats.surfaces.cline import ClineSurface


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
        checks=(),  # The builder snapshots bindings before any category
    )


def _make_skill(tmp_path: Path, name: str, body: str = "instructions") -> Path:
    """Create a fake skill source dir with a SKILL.md."""
    d = tmp_path / "sources" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n{body}\n")
    return d


def _make_runtime_hook_skill(tmp_path: Path) -> Path:
    d = tmp_path / "sources" / "guard"
    hooks = d / "hooks"
    hooks.mkdir(parents=True)
    (d / "SKILL.md").write_text(
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
    script = hooks / "guard.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return d


def test_name_is_cline() -> None:
    assert ClineSurface().name == "cline"


def test_hitl_children_inherit_authoritative_session_path() -> None:
    assert ClineSurface().supports_session_command_wrappers()


def test_get_cli_command_is_bare_binary() -> None:
    # Bare base so the HITL `-i` (added by build_session_prompt) and the automate
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
    """HATS-1554: get_env is on the report path too, where binding is a side effect."""
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
    # composed sections carry no text index (HATS-1826 removed the toggle).
    skill_path = _make_skill(tmp_path, "my-skill")
    out = ClineSurface().build_system_prompt(_fake_result(skills=[skill_path]))
    assert "## AVAILABLE SKILLS" not in out


# ---- HITL build_session_prompt through the builder --------------


def test_build_session_prompt_is_inline_interactive(tmp_path) -> None:
    provider = ClineSurface()
    args, env, meta_prompt = provider.build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(), "sid-1"
    )
    # HITL: role inline via -s. HATS-1207 moved -i out of the CONTEXT handler —
    # it is launch mode, not context, so suppressing CONTEXT must not drop the TUI.
    assert args[0] == "-s"
    # the -s value IS the persisted meta-prompt bytes (HATS-523 symmetry)
    assert args[1] == meta_prompt
    # -i now rides the launch-args seam, and still reaches the real command
    assert "-i" in provider.get_cli_launch_args(["cline", *args], "sid-1", False)
    assert "## PRIORITIES" in meta_prompt
    assert env == {}
    # Skills reach cline via --config <cache> (not a root .cline dir)
    assert "--config" in args
    cache_arg = args[args.index("--config") + 1]
    assert cache_arg == str(ProjectLayout.at(tmp_path).cache.session("sid-1"))
    # A hookless role keeps the pre-HATS-1775 launch shape.
    assert "--hooks-dir" not in args


def test_build_session_prompt_delivers_composed_runtime_hooks(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    skill = _make_runtime_hook_skill(tmp_path)

    args, env, _ = ClineSurface().build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill]), "sid-hooks"
    )

    cache = ProjectLayout.at(tmp_path).cache.session("sid-hooks")
    hooks_dir = cache / "hooks"
    assert args[args.index("--hooks-dir") + 1] == str(hooks_dir)
    assert (cache / "hooks.json").is_file()
    assert (hooks_dir / "PreToolUse").is_file()
    assert (hooks_dir / "PostToolUse").is_file()
    assert (hooks_dir / "PreToolUse").stat().st_mode & 0o111
    assert (hooks_dir / "PostToolUse").stat().st_mode & 0o111
    assert env["AI_HATS_SESSION_CACHE_DIR"] == str(cache)
    assert env["AI_HATS_PYTHON"]
    assert not (tmp_path / ".cline").exists()


def test_automate_materialization_delivers_composed_runtime_hooks(tmp_path) -> None:
    skill = _make_runtime_hook_skill(tmp_path)

    args = ClineSurface().materialize_runtime_skills(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill]), "sid-automate-hooks"
    )

    cache = ProjectLayout.at(tmp_path).cache.session("sid-automate-hooks")
    assert args == ["--config", str(cache), "--hooks-dir", str(cache / "hooks")]
    assert (cache / "hooks.json").is_file()
    assert (cache / "hooks" / "PreToolUse").is_file()
    assert not (tmp_path / ".cline").exists()


def test_a_script_missing_from_the_skill_is_a_notice_not_a_silent_drop(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    skill = _make_runtime_hook_skill(tmp_path)
    (skill / "hooks" / "guard.sh").unlink()  # safe-delete: ok tmp-fixture
    artifacts = BuiltArtifacts()

    ClineSurface().build_session_artifacts(
        ProjectLayout.at(tmp_path),
        _fake_result(skills=[skill]),
        "sid-gone",
        run_mode=RunMode.HITL,
        artifacts=artifacts,
    )

    assert not (ProjectLayout.at(tmp_path).cache.session("sid-gone") / "hooks.json").exists()
    assert len(artifacts.notices) == 2, "one per declared event, both pointing at the same file"
    assert all(
        "guard" in n and "hooks/guard.sh" in n and "will not run" in n for n in artifacts.notices
    )


def test_a_script_absent_from_the_mirror_refuses_the_build(tmp_path, monkeypatch) -> None:
    from ai_hats.hook_collection import RuntimeHookMirrorError
    from ai_hats.surfaces.cline.runtime_hooks import materialize_runtime_hooks

    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    skill = _make_runtime_hook_skill(tmp_path)
    unwritten_mirror = tmp_path / "mirror" / "skills"

    with pytest.raises(RuntimeHookMirrorError, match="guard"):
        materialize_runtime_hooks(
            ProjectLayout.at(tmp_path),
            _fake_result(skills=[skill]),
            "sid-unmirrored",
            BuiltArtifacts(),
            skills_dir=unwritten_mirror,
        )


def test_build_session_prompt_config_is_session_scoped(tmp_path) -> None:
    args_a, _, _ = ClineSurface().build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(), "sid-a"
    )
    args_b, _, _ = ClineSurface().build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(), "sid-b"
    )
    cfg_a = args_a[args_a.index("--config") + 1]
    cfg_b = args_b[args_b.index("--config") + 1]
    assert cfg_a != cfg_b
    assert "sid-a" in cfg_a and "sid-b" in cfg_b


def test_build_session_prompt_materializes_skills_to_cache(tmp_path) -> None:
    skill = _make_skill(tmp_path, "deploy-skill")
    ClineSurface().build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill]), "sid-1"
    )
    cache_skills = ProjectLayout.at(tmp_path).cache.session("sid-1") / "skills"
    assert (cache_skills / "deploy-skill" / "SKILL.md").exists()


def test_build_session_prompt_leaves_project_root_clean(tmp_path) -> None:
    # HATS-1171 clean-root: no .cline/ and no .gitignore mutation in the root.
    skill = _make_skill(tmp_path, "my-skill")
    ClineSurface().build_session_prompt(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill]), "sid-1"
    )
    assert not (tmp_path / ".cline").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_build_session_prompt_honors_context_policy(tmp_path) -> None:
    # Only-seam filtering (supervisor): policy.context=False → no -s role delivery.
    provider = ClineSurface()
    artifacts = provider.build_session_artifacts(
        ProjectLayout.at(tmp_path),
        _fake_result(),
        "sid-1",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(context=False),
        artifacts=BuiltArtifacts(),
    )
    assert "-s" not in artifacts.cli_args
    # skills category is unaffected by policy
    assert "--config" in artifacts.cli_args


def test_hookless_role_and_settings_category_add_no_launch_args(tmp_path) -> None:
    artifacts = ClineSurface().build_session_artifacts(
        ProjectLayout.at(tmp_path),
        _fake_result(),
        "sid-1",
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    assert "--settings" not in artifacts.cli_args
    assert "--hooks-dir" not in artifacts.cli_args
    assert not (tmp_path / ".cline").exists()


# ---- Automate materialize_runtime_skills through the builder -----


def test_materialize_returns_config_flag_and_writes_cache(tmp_path) -> None:
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    args = ClineSurface().materialize_runtime_skills(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    cache_dir = ProjectLayout.at(tmp_path).cache.session("sid-1")
    # Automate threads --config <cache> (no -s: role rides the meta_prompt).
    assert args == ["--config", str(cache_dir)]
    assert (cache_dir / "skills" / "skill-a" / "SKILL.md").exists()
    assert (cache_dir / "skills" / "skill-b" / "SKILL.md").exists()
    # clean root
    assert not (tmp_path / ".cline").exists()


def test_materialize_is_idempotent(tmp_path) -> None:
    skill = _make_skill(tmp_path, "my-skill")
    result = _fake_result(skills=[skill])
    provider = ClineSurface()
    provider.materialize_runtime_skills(ProjectLayout.at(tmp_path), result, "sid-1")
    cache_skills = ProjectLayout.at(tmp_path).cache.session("sid-1") / "skills"
    first = sorted(p.name for p in cache_skills.iterdir())
    provider.materialize_runtime_skills(ProjectLayout.at(tmp_path), result, "sid-1")
    second = sorted(p.name for p in cache_skills.iterdir())
    assert first == second == ["my-skill"]


def test_materialize_sessions_are_isolated(tmp_path) -> None:
    # Each session owns its own cache skills dir — no cross-session
    # sharing, so no refcount/lock dance is needed.
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineSurface()
    provider.materialize_runtime_skills(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill_a]), "sid-1"
    )
    provider.materialize_runtime_skills(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill_b]), "sid-2"
    )
    assert (ProjectLayout.at(tmp_path).cache.session("sid-1") / "skills" / "skill-a").exists()
    assert not (ProjectLayout.at(tmp_path).cache.session("sid-1") / "skills" / "skill-b").exists()
    assert (ProjectLayout.at(tmp_path).cache.session("sid-2") / "skills" / "skill-b").exists()


def test_materialize_expands_the_fsm_edges_token(tmp_path) -> None:
    """HATS-1271: cline's private copier had drifted and lost this expansion.

    The token is carried by a CORE library skill (hatrack), so a surface that
    skips it ships a SKILL.md whose own prose calls the missing table
    authoritative. Delivery is what this pins — the renderer is tested upstream.
    """
    skill = _make_skill(tmp_path, "fsm-skill", body="edges:\n\n{{backlog_fsm_edges}}\n")
    ClineSurface().materialize_runtime_skills(
        ProjectLayout.at(tmp_path), _fake_result(skills=[skill]), "sid-1"
    )

    delivered = (
        ProjectLayout.at(tmp_path).cache.session("sid-1") / "skills" / "fsm-skill" / "SKILL.md"
    ).read_text()
    assert "{{backlog_fsm_edges}}" not in delivered
    assert "brainstorm" in delivered  # a real FSM state reached the file


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
