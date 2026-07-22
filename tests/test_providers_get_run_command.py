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


def test_base_provider_model_flags() -> None:
    """Abstract base provides the default mapping."""
    flags = Provider.model_flags(None, "some-model")
    assert flags == ["--model", "some-model"]
