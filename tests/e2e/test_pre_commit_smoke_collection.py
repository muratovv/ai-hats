"""e2e (HATS-1345, HATS-1352)

flow:   a maintainer committing changes to trigger the pre-commit smoke hook
cmds:
    git commit
expect: smoke script collects fast smoke test suite and runs verification within target
        deadline
why: without fast smoke test collection, pre-commit git hooks slow down local commit
     workflows"""

from __future__ import annotations
from _helpers.git import git as _git

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


def _armed_project(tmp_path: Path, name: str) -> Path:
    """A git repo whose active task arms the smoke gate."""
    project = tmp_path / name
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")

    task_dir = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / "T-1"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text("state: execute\ntags:\n  - integration\n")
    return project


def _run_hook(
    project: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AI_HATS_SMOKE_SKIP", None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(HOOK_PATH.resolve())],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )


def test_pre_commit_smoke_passes_when_the_project_has_no_e2e_dir(tmp_path: Path) -> None:
    """HATS-1352: a consumer project without tests/e2e/ must still be able to commit.

    The hook ships to consumers via the git-mastery skill, and most have no such
    directory. Asserting on the exit code alone would also pass if the hook had
    merely learned to swallow rc=4, so the old signature is pinned as absent too.
    """
    project = _armed_project(tmp_path, "no-e2e")
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        "import pytest\n\n@pytest.mark.smoke\ndef test_ok():\n    pass\n"
    )

    res = _run_hook(project)

    assert res.returncode == 0, (
        f"the hook blocked a commit in a project with no tests/e2e/:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )
    assert "file or directory not found" not in res.stderr, (
        f"the missing path still reached pytest:\n{res.stderr}"
    )


def test_pre_commit_smoke_ignores_collection_error_outside_e2e(tmp_path: Path) -> None:
    """An unimportable module outside tests/e2e/ must not cause the hook to fail."""
    project = _armed_project(tmp_path, "proj")

    e2e_dir = project / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)
    (e2e_dir / "test_valid.py").write_text(
        "import pytest\n\n@pytest.mark.smoke\ndef test_valid():\n    pass\n"
    )

    pkg_test_dir = project / "packages" / "broken_pkg" / "tests"
    pkg_test_dir.mkdir(parents=True)
    (pkg_test_dir / "test_broken_import.py").write_text(
        "import non_existent_unimportable_module_12345\n"
    )

    res = _run_hook(project)

    assert res.returncode == 0, (
        f"Hook failed unexpectedly:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )


def test_pre_commit_smoke_survives_an_inherited_pytest_addopts(tmp_path: Path) -> None:
    """HATS-1661: an ambient PYTEST_ADDOPTS must not block the commit.

    The child pytest exits on argument parsing, and the hook — which
    special-cases only rc=5 — reports that as a test failure, though none ran.
    The flag is synthetic on purpose: the resolved pytest may HAVE xdist, so the
    gate's own ``-n8`` would parse and stay green under revert.
    """
    project = _armed_project(tmp_path, "addopts")
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        "import pytest\n\n@pytest.mark.smoke\ndef test_ok():\n    pass\n"
    )

    res = _run_hook(project, {"PYTEST_ADDOPTS": "--ai-hats-no-such-flag"})

    assert "unrecognized arguments" not in res.stderr, (
        "the inherited PYTEST_ADDOPTS reached the hook's pytest:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )
    assert res.returncode == 0, (
        "the hook blocked a commit over an inherited PYTEST_ADDOPTS:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )
