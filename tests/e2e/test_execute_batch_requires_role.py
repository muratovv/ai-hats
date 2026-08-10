"""e2e (HATS-827)

flow: a developer running execute in batch mode without specifying role or default_role
cmds:
    ai-hats execute --batch --prompt "hello"
expect: command exits with code 2 explaining that explicit role specification is
        required for batch
why: without role validation in batch mode, execution runs under uninitialized default
     roles"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_execute_batch_without_role_fails_clean(tmp_project) -> None:
    from _helpers.env import checkout_pythonpath

    prompt = tmp_project.path / "prompt.md"
    prompt.write_text("ping\n")

    result = tmp_project.run(
        "execute",
        "--batch",
        "--isolation",
        "discard",
        "--prompt",
        str(prompt),
        extra_env={"PYTHONPATH": checkout_pythonpath(_REPO_ROOT)},
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
