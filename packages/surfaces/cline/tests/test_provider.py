"""Contract tests for ``ClineProvider`` (HATS-956).

Pure-method assertions (no real cline, no auth): the CLI-shape, env, and inline
`-s` role delivery the ai-hats runners depend on.
"""

from __future__ import annotations

from types import SimpleNamespace

from ai_hats_cline import ClineProvider


def _fake_result() -> SimpleNamespace:
    """A minimal duck-typed ``CompositionResult`` for ``_compose_sections``."""
    return SimpleNamespace(
        priorities=["Reliability"],
        merged_injection="## ROLE\nbody",
        rules=[],
        skills=[],
    )


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
    assert env == {}
