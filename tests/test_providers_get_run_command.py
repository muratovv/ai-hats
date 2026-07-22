"""Provider.get_run_command — model override behaviour (HATS-232)."""

from __future__ import annotations

from ai_hats.surfaces.claude.provider import ClaudeProvider, Provider
from ai_hats_agy.provider import AgyProvider


def test_claude_model_flags() -> None:
    p = ClaudeProvider()
    assert p.model_flags("claude-haiku-4-5") == ["--model", "claude-haiku-4-5"]
    assert p.model_flags("") == ["--model", ""]

def test_claude_get_run_command() -> None:
    p = ClaudeProvider()
    assert p.get_run_command(["claude"], "hello") == ["claude", "--print", "-p", "hello"]
    # With flags appended by harness
    assert p.get_run_command(["claude", "--model", "claude-haiku-4-5"], "hello") == [
        "claude", "--model", "claude-haiku-4-5", "--print", "-p", "hello",
    ]


def test_agy_get_run_command() -> None:
    p = AgyProvider()
    assert p.get_run_command(["agy"], "hi") == ["agy", "-p", "hi"]
    # With flags appended by harness
    assert p.get_run_command(["agy", "--model", "agy-2.0-flash"], "hi") == [
        "agy", "--model", "agy-2.0-flash", "-p", "hi",
    ]


def test_non_claude_provider_has_launch_args_default() -> None:
    """Every provider answers get_cli_launch_args (HATS-1130).

    wrap_runner calls it unconditionally; before ec85f43d the --session-id
    injection was gated on ``provider_name == PROVIDER_CLAUDE``, so a base
    default returning the command unchanged restores those semantics exactly.
    Without it ``ai-hats -p agy`` dies with AttributeError before launch.
    """
    p = AgyProvider()
    assert p.get_cli_launch_args(["agy"], "sid-123", False) == ["agy"]
    assert p.get_cli_launch_args(["agy"], "sid-123", True) == ["agy"]


def test_claude_still_injects_session_id() -> None:
    p = ClaudeProvider()
    assert p.get_cli_launch_args(["claude"], "sid-123", False) == [
        "claude", "--session-id", "sid-123",
    ]
    assert p.get_cli_launch_args(["claude"], "sid-123", True) == ["claude"]


def test_base_provider_model_flags() -> None:
    """Abstract base provides the default mapping."""
    flags = Provider.model_flags(None, "some-model")
    assert flags == ["--model", "some-model"]
