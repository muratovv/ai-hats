"""Unit tests for ``ai_hats.surfaces.claude.sdk_options``: how a record names
an option document."""

from __future__ import annotations

from ai_hats.surfaces.claude.sdk_options import describe_options


# ---------------------------------------------------------------------------
# describe_options
# ---------------------------------------------------------------------------


def test_describe_options_names_what_ai_hats_set_and_hides_env_values() -> None:
    from claude_agent_sdk import ClaudeAgentOptions

    described = describe_options(
        ClaudeAgentOptions(cwd="/wt", model="m", env={"B": "secret", "A": "secret"})
    )

    assert described == ["env=['A', 'B']", "model=m"], "cwd omitted, env by key name only"
