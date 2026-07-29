"""E2E acceptance test for HATS-1345: pre-commit smoke hook targets tests/e2e/.

Ensures that collection errors in modules outside tests/e2e/ do NOT cause
pre-commit-smoke.sh to fail.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.smoke]

HOOK_PATH = (
    Path(__file__).parent.parent.parent
    / "packages"
    / "ai-hats-library"
    / "src"
    / "ai_hats_library"
    / "core"
    / "skills"
    / "git-mastery"
    / "git_hooks"
    / "pre-commit-smoke.sh"
)


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


def test_pre_commit_smoke_ignores_collection_error_outside_e2e(tmp_path: Path) -> None:
    """An unimportable module outside tests/e2e/ must not cause the hook to fail."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")

    task_dir = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / "T-1"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text("state: execute\ntags:\n  - integration\n")

    # Create tests/e2e with a passing smoke test
    e2e_dir = project / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)
    (e2e_dir / "test_valid.py").write_text(
        "import pytest\n\n@pytest.mark.smoke\ndef test_valid():\n    pass\n"
    )

    # Create a package outside tests/e2e/ with a broken/unimportable test file
    pkg_test_dir = project / "packages" / "broken_pkg" / "tests"
    pkg_test_dir.mkdir(parents=True)
    (pkg_test_dir / "test_broken_import.py").write_text(
        "import non_existent_unimportable_module_12345\n"
    )

    env = os.environ.copy()
    env.pop("AI_HATS_SMOKE_SKIP", None)

    res = subprocess.run(
        ["bash", str(HOOK_PATH.resolve())],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"Hook failed unexpectedly:\nstdout: {res.stdout}\nstderr: {res.stderr}"
