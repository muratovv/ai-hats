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
import sys


def assert_command_exists(*path: str) -> None:
    """Assert the ``ai-hats <path>`` click command exists (e.g. ``("self", "init")``).

    Runs ``python -m ai_hats <path> --help`` and asserts exit 0. Click resolves
    ``--help`` before any callback, so there are no side effects beyond argv
    parsing. Mark callers ``@pytest.mark.integration`` — this spawns a subprocess.

    Raises:
        AssertionError: command path does not resolve; message carries stderr.
    """
    # HATS-922: invoke via `python -m ai_hats`, not the bare `ai-hats` binary —
    # there is no console script (HATS-790), so the binary is absent in CI's
    # `pip install -e` env and this stayed green only on dev PATH.
    if not path:
        raise ValueError("assert_command_exists requires at least one path segment")

    result = subprocess.run(
        [sys.executable, "-m", "ai_hats", *path, "--help"],
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
