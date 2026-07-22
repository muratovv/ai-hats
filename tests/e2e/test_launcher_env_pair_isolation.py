"""E2E (HATS-1123): the launcher drops a foreign ``AI_HATS_DIR`` when it
re-pins ``AI_HATS_PROJECT_DIR``.

``dev_rule_e2e_gate`` artifact for ``scripts/ai-hats-launcher``. The pair must
move together: the launcher sets ``PROJECT="$(pwd)"`` and exports
``AI_HATS_PROJECT_DIR="$PROJECT"``, so an inherited ``AI_HATS_DIR`` aimed at the
previous project would survive the re-pin and make THIS project's commands
(migrations, hook materialization) read and write the OTHER project's
``.agent/ai-hats``. That divergence is what let a sub-agent shell in a worktree
partition the main checkout's hooks.

Real bash + the real launcher (session-shared venv). Fail-under-revert: drop the
``unset AI_HATS_DIR`` branch and ``config status`` reports the foreign dir.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_launcher_drops_foreign_ai_hats_dir_on_repin(
    shared_launcher, tmp_path: Path
) -> None:
    launcher, base_env, _venv = shared_launcher

    foreign = tmp_path / "other-project"
    (foreign / ".agent" / "ai-hats").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()

    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    # The leaked pair: pinned to `foreign`, but we run from `project`.
    env["AI_HATS_DIR"] = str((foreign / ".agent" / "ai-hats").resolve())
    env["AI_HATS_PROJECT_DIR"] = str(foreign.resolve())

    res = subprocess.run(  # noqa: S603 — fixed argv, launcher under test
        [str(launcher), "self", "init", "-p", "claude"],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=300,
    )
    out = res.stdout + res.stderr

    # Behavioural discriminator: ai_hats_dir() decides where init materializes.
    # Keep the foreign pin and this project's init populates the OTHER
    # project's namespace instead of its own.
    foreign_ns = foreign / ".agent" / "ai-hats"
    assert list(foreign_ns.iterdir()) == [], (
        f"launcher kept a foreign AI_HATS_DIR after re-pinning the project, so "
        f"this project's init wrote into {foreign_ns} (HATS-1123):\n{out}"
    )
    assert (project / ".agent" / "ai-hats" / "library").is_dir(), (
        f"init did not materialize into the project's own namespace:\n{out}"
    )
    assert "HATS-1123" in res.stderr, (
        f"expected the launcher to announce the dropped pin:\n{out}"
    )
