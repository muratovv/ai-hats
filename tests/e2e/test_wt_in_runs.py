"""e2e (HATS-823)

flow:   a developer creating a worktree when wt_in lifecycle hooks are registered
cmds:
    ai-hats wt create task/probe
expect: wt_in lifecycle hook executes during worktree creation and populates initial
        files
why:    wt_in hook must fire during worktree setup to provision required environment
        state"""

from __future__ import annotations
from _helpers.git import git as _git

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "wt_hook_lib"


def _run(cmd, *, cwd, env, timeout=180, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_wt_in_runs_after_worktree_add(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _git(project, "add", "libraries")
    _git(project, "commit", "-m", "lib")
    _run(
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "e2e-wthook-role",
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=project,
        env=env,
    )

    _run([str(launcher), "wt", "create", "task/seedprobe"], cwd=project, env=env)

    seeded = project / ".seeded"
    assert seeded.exists(), "wt_in hook did not run"
    recorded = seeded.read_text().strip()
    # The hook was handed the linked worktree path (post-add), not project root.
    assert recorded != str(project)
    assert "ai-hats-wt-" in recorded
