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
    assert ClineProvider().get_env(tmp_path / "session", tmp_path) == {}


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
    assert "## PRIORITIES" in meta_prompt
    assert env == {}


# ---- HATS-963: .cline/skills/ materialization ----


def test_build_system_prompt_suppresses_skills_index(tmp_path) -> None:
    # HATS-963: skills delivered via .cline/skills/ native registry, not text
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


def test_materialize_sweeps_stale_skills(tmp_path) -> None:
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = ClineProvider()
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    provider.materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a]), "sid-2"
    )
    assert (tmp_path / ".cline" / "skills" / "skill-a").exists()
    assert not (tmp_path / ".cline" / "skills" / "skill-b").exists()


def test_materialize_marker_lists_managed_skills(tmp_path) -> None:
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    ClineProvider().materialize_runtime_skills(
        tmp_path, _fake_result(skills=[skill_a, skill_b]), "sid-1"
    )
    marker = (tmp_path / ".cline" / "skills" / ".ai-hats-managed").read_text()
    assert "skill-a" in marker
    assert "skill-b" in marker
    assert "user-skill" not in marker


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
