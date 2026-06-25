"""E2E: ``ai-hats execute --batch`` without a role fails clean at the CLI
boundary and redirects to ``ai-hats agent`` (HATS-827).

``execute`` is the low-level primitive; ``--batch`` builds an isolated worktree
whose branch is ``agent/<role>/<sid>``. An empty role previously produced the
git-invalid refname ``agent//<sid>`` and crashed deep in worktree creation
(``WorktreeCreateError``, 9-frame traceback) — blocking every autonomous batch
sub-agent launched without ``-r``. The fix guards at the CLI boundary BEFORE the
pipeline runs, so no worktree is attempted and the message names the recommended
surface (``ai-hats agent <role>``).

No LLM call: the guard fires before any provider launch — fast and free.

Worktree-portable: pins ``PYTHONPATH`` to this checkout's ``src`` so the shim
subprocess exercises the code under test (the autouse ``_scrub_redirect_env``
fixture otherwise routes subprocesses to the installed build). The guard runs
before any ``ai_hats.library`` access, so a raw-``src`` import is sufficient.
Fails-under-revert: drop the guard and the subprocess reverts to the
``WorktreeCreateError`` crash, tripping the no-traceback assertions.
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
