"""Shared CLI test helpers.

Lightweight checks that real `ai-hats` click commands exist at the expected
paths. Used to catch the "command moved between groups" bug class without
spinning up a full E2E install flow (real bash + real pip + real ai-hats),
which is covered by ``tests/e2e/``.

The leading underscore in the module name keeps pytest from collecting this
file as a test module.
"""

from __future__ import annotations

import subprocess


def assert_command_exists(*path: str) -> None:
    """Assert ``ai-hats <path>`` exists in the installed click tree.

    Examples::

        assert_command_exists("agent")                  # top-level
        assert_command_exists("self", "init")           # 2-level
        assert_command_exists("task", "hyp", "create")  # 3-level

    Invokes ``ai-hats <path> --help`` and asserts the subprocess exits 0.
    Click resolves ``--help`` before any command callback runs, so this has
    no side effects beyond argv parsing.

    Tests using this helper must be marked ``@pytest.mark.integration`` —
    it spawns a real ``ai-hats`` subprocess.

    Raises:
        AssertionError: command path does not resolve. Message includes the
            full path and captured stderr.
    """
    if not path:
        raise ValueError("assert_command_exists requires at least one path segment")

    result = subprocess.run(
        ["ai-hats", *path, "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        joined = " ".join(path)
        raise AssertionError(
            f"`ai-hats {joined}` does not exist "
            f"(exit={result.returncode}).\nstderr:\n{result.stderr}"
        )
