"""e2e (HATS-1123)

flow: a developer running self init in a new project directory while AI_HATS_DIR
      environment
        variable points to another project
cmds:
    ai-hats self init -p claude
expect: launcher unsets foreign AI_HATS_DIR when repinning AI_HATS_PROJECT_DIR to
        current
        directory
why: without unsetting foreign AI_HATS_DIR, commands in a new project overwrite
     configuration
        and hooks in the foreign project"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.install]


def test_launcher_drops_foreign_ai_hats_dir_on_repin(shared_launcher, tmp_path: Path) -> None:
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
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
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
    assert "dropping it" in res.stderr, f"expected the launcher to announce the dropped pin:\n{out}"
