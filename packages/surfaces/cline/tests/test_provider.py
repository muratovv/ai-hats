"""Contract tests for ``ClineProvider`` (HATS-956, HATS-963).

Pure-method assertions (no real cline, no auth): the CLI-shape, env, inline
`-s` role delivery, and `.cline/skills/` materialization the ai-hats runners
depend on.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ai_hats_cline import ClineProvider


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
    )


def _make_skill(tmp_path: Path, name: str, body: str = "instructions") -> Path:
    """Create a fake skill source dir with a SKILL.md."""
    d = tmp_path / "sources" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n{body}\n")
    return d


def test_name_is_cline() -> None:
    assert ClineProvider().name == "cline"


def test_get_cli_command_is_bare_binary() -> None:
    # Bare base so the HITL `-i` (added by build_session_prompt) and the automate
    # `--yolo` (added by get_run_command) never collide.
    provider = ClineProvider()
    assert provider.get_cli_command() == ["cline"]
    assert provider.get_cli_command(["-c", "sub/dir"]) == ["cline", "-c", "sub/dir"]


def test_get_run_command_is_headless_yolo() -> None:
    cmd = ClineProvider().get_run_command(["cline"], "do the thing")
    assert cmd == ["cline", "--yolo", "--json", "do the thing"]
    # mutually-exclusive interactive flag must never appear on the headless path
    assert "-i" not in cmd and "--tui" not in cmd
    # ai-hats-wt owns isolation — cline must not fork its own worktree
    assert "--worktree" not in cmd


def test_get_run_command_threads_model() -> None:
    cmd = ClineProvider().get_run_command(["cline"], "task", model="glm-5.2")
    assert cmd == ["cline", "--yolo", "--json", "--model", "glm-5.2", "task"]
    # task prompt stays positional-last
    assert cmd[-1] == "task"


def test_get_run_command_drops_stale_interactive_base() -> None:
    # Even if a `-i` base leaks in, the headless rebuild strips it.
    cmd = ClineProvider().get_run_command(["cline", "-i"], "task")
    assert cmd == ["cline", "--yolo", "--json", "task"]


def test_get_run_command_preserves_passthrough_args() -> None:
    # Non-interactive passthrough (e.g. future skill args) survives the rebuild.
    cmd = ClineProvider().get_run_command(["cline", "--plugin-dir", "/x"], "task")
    assert cmd == ["cline", "--plugin-dir", "/x", "--yolo", "--json", "task"]


def test_get_env_does_not_isolate_data_dir(tmp_path) -> None:
    # R10: isolating CLINE_DATA_DIR would cut off the machine's cline auth.
    # R7 (HATS-964): AI_HATS_DIR is needed so the TS plugin can find guard scripts.
    env = ClineProvider().get_env(tmp_path / "session", tmp_path)
    assert "CLINE_DATA_DIR" not in env
    assert env["AI_HATS_DIR"]
    assert env["AI_HATS_PROJECT_DIR"] == str(tmp_path)


def test_get_env_sets_cline_hub_port(tmp_path) -> None:
    # HATS-973: per-session CLINE_HUB_PORT moves each ai-hats cline session off
    # the default hub port (25463) so parallel sessions don't collide.
    env = ClineProvider().get_env(tmp_path / "session", tmp_path)
    port = int(env["CLINE_HUB_PORT"])
    assert 1024 < port < 65536


def test_get_env_distinct_sessions_distinct_ports(tmp_path) -> None:
    # Two sessions must own different hub ports (ephemeral allocation).
    env_a = ClineProvider().get_env(tmp_path / "sess-a", tmp_path)
    env_b = ClineProvider().get_env(tmp_path / "sess-b", tmp_path)
    assert env_a["CLINE_HUB_PORT"] != env_b["CLINE_HUB_PORT"]


def test_update_system_prompt_is_noop(tmp_path) -> None:
    # Inline-only surface: set_role must not litter a CLINE.md cline ignores.
    ClineProvider().update_system_prompt(tmp_path, "role body")
    assert not (tmp_path / "CLINE.md").exists()


def test_build_system_prompt_composes_sections() -> None:
    out = ClineProvider().build_system_prompt(_fake_result())
    assert "## PRIORITIES" in out
    assert "1. Reliability" in out
    assert "## ROLE" in out


def test_build_session_prompt_is_inline_interactive(tmp_path) -> None:
    args, env, meta_prompt = ClineProvider().build_session_prompt(
        tmp_path, _fake_result(), "sid-1"
    )
    # HITL: interactive TUI + role inline via -s
    assert args[0] == "-i"
    assert args[1] == "-s"
    # the -s value IS the persisted meta-prompt bytes (HATS-523 symmetry)
    assert args[2] == meta_prompt
    assert "## PRIORITIES" in meta_prompt
    assert env == {}


# ---- HATS-963: .cline/skills/ materialization ----


def test_build_system_prompt_suppresses_skills_index(tmp_path) -> None:
    # HATS-963: skills delivered via .cline/skills/ native registry (confirmed
    # by live smoke). The text index is suppressed — Claude precedent
    # (providers.py:420-424, include_skills=False).
    skill_path = _make_skill(tmp_path, "my-skill")
    out = ClineProvider().build_system_prompt(_fake_result(skills=[skill_path]))
    assert "## AVAILABLE SKILLS" not in out


def test_materialize_writes_skills_to_cline_dir(tmp_path) -> None:
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    ClineProvider().materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    skills_dir = tmp_path / ".cline" / "skills"
    assert (skills_dir / "skill-a" / "SKILL.md").exists()
    assert (skills_dir / "skill-b" / "SKILL.md").exists()


def test_materialize_is_idempotent(tmp_path) -> None:
    skill = _make_skill(tmp_path, "my-skill")
    result = _fake_result(skills=[skill])
    ClineProvider().materialize_runtime_skills(tmp_path, result, "sid-1")
    first = sorted(p.name for p in (tmp_path / ".cline" / "skills").iterdir())
    ClineProvider().materialize_runtime_skills(tmp_path, result, "sid-2")
    second = sorted(p.name for p in (tmp_path / ".cline" / "skills").iterdir())
    assert first == second


def test_materialize_preserves_user_skills(tmp_path) -> None:
    user_skill = tmp_path / ".cline" / "skills" / "user-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("---\nname: user-skill\n---\nmine\n")
    ai_skill = _make_skill(tmp_path, "ai-skill")
    ClineProvider().materialize_runtime_skills(
        tmp_path, _fake_result(skills=[ai_skill]), "sid-1"
    )
    assert (user_skill / "SKILL.md").read_text() == "---\nname: user-skill\n---\nmine\n"
    assert (tmp_path / ".cline" / "skills" / "ai-skill" / "SKILL.md").exists()


def test_materialize_same_session_sweeps_stale(tmp_path) -> None:
    # HATS-981: a single session changing its skills sweeps stale ones.
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineProvider()
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    # SAME session_id — role dropped skill-b.
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a]), "sid-1"
    )
    assert (tmp_path / ".cline" / "skills" / "skill-a").exists()
    assert not (tmp_path / ".cline" / "skills" / "skill-b").exists()


def test_materialize_different_sessions_preserve_each_other(tmp_path) -> None:
    # HATS-981 R2: different sessions must NOT wipe each other's skills.
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineProvider()
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    # DIFFERENT session_id — sid-2 doesn't want skill-b, but sid-1 still does.
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a]), "sid-2"
    )
    assert (tmp_path / ".cline" / "skills" / "skill-a").exists()
    assert (tmp_path / ".cline" / "skills" / "skill-b").exists()


def test_materialize_parallel_threads_both_skills_present(tmp_path) -> None:
    # HATS-981 R2 key test: two threads, different roles, both skills survive.
    import threading

    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineProvider()
    errors: list[Exception] = []

    def _materialize(skills, sid):
        try:
            provider.materialize_runtime_skills(
                tmp_path, _fake_result(skills=skills), sid
            )
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_materialize, args=([skill_a], "sid-1"))
    t2 = threading.Thread(target=_materialize, args=([skill_b], "sid-2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, errors
    skills_dir = tmp_path / ".cline" / "skills"
    assert (skills_dir / "skill-a" / "SKILL.md").exists()
    assert (skills_dir / "skill-b" / "SKILL.md").exists()


def test_materialize_marker_is_session_refcounted(tmp_path) -> None:
    import json

    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineProvider()
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a]), "sid-2"
    )
    marker = json.loads(
        (tmp_path / ".cline" / "skills" / ".ai-hats-managed").read_text()
    )
    assert marker["sid-1"] == ["skill-a", "skill-b"]
    assert marker["sid-2"] == ["skill-a"]


def test_materialize_returns_no_cli_args(tmp_path) -> None:
    skill = _make_skill(tmp_path, "my-skill")
    args = ClineProvider().materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill]), "sid-1"
    )
    assert args == []


def test_build_session_prompt_materializes_skills(tmp_path) -> None:
    skill = _make_skill(tmp_path, "deploy-skill")
    ClineProvider().build_session_prompt(
        tmp_path, _fake_result(skills=[skill]), "sid-1"
    )
    assert (tmp_path / ".cline" / "skills" / "deploy-skill" / "SKILL.md").exists()


def test_materialize_gitignores_cline_skills(tmp_path) -> None:
    # R4c: the materialized mirror must not surface as untracked.
    skill = _make_skill(tmp_path, "my-skill")
    ClineProvider().materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill]), "sid-1"
    )
    gitignore = (tmp_path / ".gitignore").read_text()
    assert ".cline/skills/" in gitignore


def test_materialize_gitignore_is_idempotent(tmp_path) -> None:
    # Re-materialization must not duplicate the .gitignore entry.
    skill = _make_skill(tmp_path, "my-skill")
    provider = ClineProvider()
    provider.materialize_runtime_skills(tmp_path, _fake_result(skills=[skill]), "s1")
    provider.materialize_runtime_skills(tmp_path, _fake_result(skills=[skill]), "s2")
    gitignore = (tmp_path / ".gitignore").read_text()
    assert gitignore.count(".cline/skills/") == 1


# ---- HATS-964: .cline/plugins/ hooks materialization ----


def test_ensure_runtime_hooks_writes_plugin(tmp_path) -> None:
    ClineProvider().ensure_runtime_hooks(tmp_path)
    plugin = tmp_path / ".cline" / "plugins" / "ai-hats-hooks.ts"
    assert plugin.exists()
    # the plugin must export a default AgentPlugin with hooks
    ts = plugin.read_text()
    assert "export default plugin" in ts
    assert "beforeTool" in ts


def test_ensure_runtime_hooks_writes_index(tmp_path) -> None:
    import json

    ClineProvider().ensure_runtime_hooks(tmp_path)
    index = json.loads(
        (tmp_path / ".cline" / "plugins" / "ai-hats-hooks.json").read_text()
    )
    # guard entry is unconditional
    assert len(index) >= 1
    guard = index[0]
    assert guard["event"] == "PreToolUse"
    assert guard["cline_tool"] == "bash"
    assert guard["script"] == "pre_bash_shared_state_guard.sh"


def test_ensure_runtime_hooks_gitignores_plugins(tmp_path) -> None:
    ClineProvider().ensure_runtime_hooks(tmp_path)
    gitignore = (tmp_path / ".gitignore").read_text()
    assert ".cline/plugins/" in gitignore


def test_ensure_runtime_hooks_is_idempotent(tmp_path) -> None:
    provider = ClineProvider()
    provider.ensure_runtime_hooks(tmp_path)
    first = (tmp_path / ".cline" / "plugins" / "ai-hats-hooks.ts").read_text()
    provider.ensure_runtime_hooks(tmp_path)
    second = (tmp_path / ".cline" / "plugins" / "ai-hats-hooks.ts").read_text()
    assert first == second
    # no duplicate gitignore entries
    gitignore = (tmp_path / ".gitignore").read_text()
    assert gitignore.count(".cline/plugins/") == 1


def test_ensure_runtime_hooks_sweeps_stale(tmp_path) -> None:
    plugins_dir = tmp_path / ".cline" / "plugins"
    plugins_dir.mkdir(parents=True)
    stale = plugins_dir / "old-plugin.ts"
    stale.write_text("// orphan from previous role")
    marker = plugins_dir / ".ai-hats-managed"
    marker.write_text("old-plugin.ts\n")

    ClineProvider().ensure_runtime_hooks(tmp_path)
    assert not stale.exists()
    assert (plugins_dir / "ai-hats-hooks.ts").exists()


def test_build_session_prompt_materializes_hooks(tmp_path) -> None:
    ClineProvider().build_session_prompt(tmp_path, _fake_result(), "sid-1")
    assert (tmp_path / ".cline" / "plugins" / "ai-hats-hooks.ts").exists()
    assert (tmp_path / ".cline" / "plugins" / "ai-hats-hooks.json").exists()


def test_guard_bridge_blocks_irreversible(tmp_path) -> None:
    """R1: stdin the plugin produces → real guard → blocks force-push (exit 2)."""
    import json
    import subprocess

    repo_root = Path(__file__).resolve().parents[4]
    guard = repo_root / "library" / "hooks" / "pre_bash_shared_state_guard.sh"
    if not guard.exists():
        return  # running outside monorepo
    stdin = json.dumps({"tool_input": {"command": "git push --force origin main"}})
    res = subprocess.run(
        ["bash", str(guard)], input=stdin, capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 2
    assert "BLOCKED" in res.stderr


def test_guard_bridge_allows_safe(tmp_path) -> None:
    """R1: safe commands pass through the guard (exit 0)."""
    import json
    import subprocess

    repo_root = Path(__file__).resolve().parents[4]
    guard = repo_root / "library" / "hooks" / "pre_bash_shared_state_guard.sh"
    if not guard.exists():
        return
    stdin = json.dumps({"tool_input": {"command": "echo hello"}})
    res = subprocess.run(
        ["bash", str(guard)], input=stdin, capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
