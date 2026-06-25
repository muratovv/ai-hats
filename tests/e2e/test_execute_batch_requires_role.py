"""E2E: ``ai-hats execute --batch`` without a role fails clean (HATS-827).

The guard fails at the CLI boundary (redirect to ``ai-hats agent``) before any
provider launch — no LLM call. Pins ``PYTHONPATH`` to this checkout's ``src`` so
the shim subprocess runs the code under test; the guard runs before any library
access, so a raw-``src`` import suffices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def test_execute_batch_without_role_fails_clean(tmp_project) -> None:
    prompt = tmp_project.path / "prompt.md"
    prompt.write_text("ping\n")

    result = tmp_project.run(
        "execute", "--batch", "--isolation", "discard",
        "--prompt", str(prompt),
        extra_env={"PYTHONPATH": str(_REPO_SRC)},
    ).expect_failure()

    # Click usage error → exit 2 (stable contract; see how-to-orchestration.md).
    assert result.exit_code == 2, result.stderr

    # Clean, actionable boundary error that names the recommended surface.
    assert "--batch requires a role" in result.stderr
    assert "ai-hats agent" in result.stderr

    # NOT the deep worktree crash the guard replaces.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert "WorktreeCreateError" not in combined
    assert "agent//" not in combined
