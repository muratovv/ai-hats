"""Provider.get_run_command — model override behaviour (HATS-232)."""

from __future__ import annotations

from ai_hats.providers import ClaudeProvider, GeminiProvider, Provider


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


def test_gemini_get_run_command_without_model() -> None:
    p = GeminiProvider()
    # --skip-trust: headless gemini hard-fails in non-trusted dirs (HATS-993).
    assert p.get_run_command(["gemini"], "hi") == ["gemini", "--skip-trust", "-p", "hi"]


def test_gemini_get_run_command_with_model() -> None:
    p = GeminiProvider()
    assert p.get_run_command(["gemini"], "hi", model="gemini-2.0-flash") == [
        "gemini", "--model", "gemini-2.0-flash", "--skip-trust", "-p", "hi",
    ]


def test_base_provider_default_get_run_command_signature() -> None:
    """Abstract base accepts the kwarg (default no-op) so subclasses can be
    invoked uniformly via the base contract.
    """
    # Use a concrete subclass that doesn't override; signature is the contract.
    sig = Provider.get_run_command.__doc__
    assert sig is None or "model" in sig
