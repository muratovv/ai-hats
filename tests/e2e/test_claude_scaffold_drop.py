"""e2e (HATS-1170, HATS-1201)

flow:   a developer updating framework version on a project with orphan CLAUDE.md
        scaffolds
cmds:
    ai-hats self update
expect: framework update removes orphan CLAUDE.md scaffold while preserving user content
why:    without migration step 7, legacy root CLAUDE.md scaffolds persist after being
        deprecated
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import PROJECT_CONFIG

pytestmark = [pytest.mark.library, pytest.mark.surfaces]


SCAFFOLD = "<!-- ai-hats:start -->\n@./.agent/ai-hats/imports.md\n<!-- ai-hats:end -->\n"


def _bump(venv: Path, project: Path, env: dict[str, str]):
    result = subprocess.run(
        [f"{venv}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"bump failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _seed_upgrading_project(project: Path, claude_md: str) -> None:
    """A v4 project from before HATS-471 — no ``migration_step``, so the
    registry replays from 0 on the next bump."""
    project.mkdir(parents=True, exist_ok=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    (project / "CLAUDE.md").write_text(claude_md)


@pytest.mark.integration
def test_e2e_bump_drops_pure_scaffold_claude_md(shared_launcher, tmp_path):
    _launcher, env, venv = shared_launcher
    project = tmp_path / "orphan_scaffold"
    _seed_upgrading_project(project, SCAFFOLD)

    _bump(venv, project, env)

    assert not (project / "CLAUDE.md").exists(), (
        "bump left the orphaned ai-hats scaffold in root CLAUDE.md"
    )


@pytest.mark.integration
def test_e2e_bump_preserves_user_content_and_is_idempotent(shared_launcher, tmp_path):
    _launcher, env, venv = shared_launcher
    project = tmp_path / "user_content"
    _seed_upgrading_project(project, SCAFFOLD + "\n# House rules\n\nMine, not yours.\n")

    _bump(venv, project, env)

    after_first = (project / "CLAUDE.md").read_text()
    assert after_first == "# House rules\n\nMine, not yours.\n"

    _bump(venv, project, env)

    assert (project / "CLAUDE.md").read_text() == after_first
