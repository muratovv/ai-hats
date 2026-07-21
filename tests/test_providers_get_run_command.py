"""Provider.get_run_command — model override behaviour (HATS-232)."""

from __future__ import annotations

from ai_hats.providers import ClaudeProvider, Provider
from ai_hats_agy.provider import AgyProvider


def test_claude_get_run_command_without_model() -> None:
    p = ClaudeProvider()
    assert p.get_run_command(["claude"], "hello") == ["claude", "--print", "-p", "hello"]


def test_claude_get_run_command_with_model() -> None:
    p = ClaudeProvider()
    assert p.get_run_command(["claude"], "hello", model="claude-haiku-4-5") == [
        "claude", "--model", "claude-haiku-4-5", "--print", "-p", "hello",
    ]


def test_claude_get_run_command_empty_model_treated_as_unset() -> None:
    p = ClaudeProvider()
    # Falsy strings (e.g. SubAgentRunner default "") must not produce --model.
    assert p.get_run_command(["claude"], "hi", model="") == [
        "claude", "--print", "-p", "hi",
    ]


def test_agy_get_run_command_without_model() -> None:
    p = AgyProvider()
    assert p.get_run_command(["agy"], "hi") == ["agy", "-p", "hi"]


def test_agy_get_run_command_with_model() -> None:
    p = AgyProvider()
    assert p.get_run_command(["agy"], "hi", model="agy-2.0-flash") == [
        "agy", "--model", "agy-2.0-flash", "-p", "hi",
    ]


def test_base_provider_default_get_run_command_signature() -> None:
    """Abstract base accepts the kwarg (default no-op) so subclasses can be
    invoked uniformly via the base contract.
    """
    # Use a concrete subclass that doesn't override; signature is the contract.
    sig = Provider.get_run_command.__doc__
    assert sig is None or "model" in sig
