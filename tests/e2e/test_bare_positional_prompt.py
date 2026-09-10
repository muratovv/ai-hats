"""e2e (HATS-1202)

flow:   a developer running ai-hats with a bare positional prompt argument
cmds:
    ai-hats -p nonexistent_provider_1202 "hello world"
expect: CLI parses positional argument as prompt rather than complaining of unknown
        subcommand
why:    without positional prompt parsing, user prompts without explicit flags fail as
        unknown subcommands
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bare_positional_prompt_does_not_fail_with_no_such_command(tmp_project) -> None:
    from _helpers.env import checkout_pythonpath

    result = tmp_project.run(
        "-p",
        "nonexistent_provider_1202",
        "hello world",
        extra_env={"PYTHONPATH": checkout_pythonpath(_REPO_ROOT)},
        timeout=10.0,
    ).expect_failure()

    combined = result.stdout + result.stderr
    assert "Error: No such command 'hello world'" not in combined
    assert "Provider 'nonexistent_provider_1202' not found" in combined
