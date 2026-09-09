"""e2e (HATS-1593)

flow:   two agents creating worktrees on DIFFERENT branches at the same time,
        while the role's wt_in hook takes longer than the create lock's budget
cmds:
    ai-hats wt create task/probe-a   # concurrently with
    ai-hats wt create task/probe-b
expect: both creates succeed; neither is refused by the create lock
why:    wt_in is documented at 45s but ran inside the repo-wide create lock,
        whose budget is 10s — a peer on an unrelated branch was refused."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git as _git

pytestmark = pytest.mark.wt

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "wt_hook_lib"

# Above CREATE_LOCK_TIMEOUT (10.0) with room for the launcher's startup, so the
# second process reaches the lock while the first still holds it.
HOOK_SLEEP_S = 14

SLOW_HOOK = """#!/usr/bin/env bash
# HATS-1593 e2e fixture: a wt_in hook that outlives the create lock's budget.
set -e
sleep {sleep_s}
echo "$AI_HATS_WORKTREE_PATH" > "$AI_HATS_PROJECT_DIR/.seeded-${{AI_HATS_BRANCH_NAME//\\//-}}"
"""

CREATE_LOCK_REFUSAL = "wt create lock held by another process"


def _init_project(project: Path, launcher: Path, env: dict) -> None:
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    shutil.copytree(FIXTURE_LIB, project / "libraries")
    hook = project / "libraries" / "skills" / "e2e-wthook" / "hooks" / "seed.sh"
    hook.write_text(SLOW_HOOK.format(sleep_s=HOOK_SLEEP_S))
    hook.chmod(0o755)
    _git(project, "add", "libraries")
    _git(project, "commit", "-m", "lib")

    init = subprocess.run(
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
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert init.returncode == 0, f"self init failed:\n{init.stdout}\n{init.stderr}"


@pytest.mark.integration
def test_concurrent_creates_survive_a_hook_longer_than_the_create_lock(
    installed_launcher, tmp_path
):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init_project(project, launcher, env)

    # Both spawned before either can finish: the loser of the create-lock race
    # is the process this regression is about.
    procs = {
        branch: subprocess.Popen(
            [str(launcher), "wt", "create", branch],
            cwd=str(project),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for branch in ("task/probe-a", "task/probe-b")
    }
    results = {}
    for branch, proc in procs.items():
        stdout, stderr = proc.communicate(timeout=HOOK_SLEEP_S * 6)
        results[branch] = (proc.returncode, stdout, stderr)

    for branch, (rc, stdout, stderr) in results.items():
        assert CREATE_LOCK_REFUSAL not in (stdout + stderr), (
            f"{branch} was refused by the repo-wide create lock while a peer ran "
            f"its wt_in hook — the hook must not be held under that lock.\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
        assert rc == 0, f"{branch} exited {rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"

    listed = _git(project, "worktree", "list", "--porcelain").stdout
    for branch in ("task/probe-a", "task/probe-b"):
        assert f"refs/heads/{branch}" in listed, f"{branch} has no linked worktree:\n{listed}"
        seeded = project / f".seeded-{branch.replace('/', '-')}"
        assert seeded.exists(), f"wt_in hook did not complete for {branch}"
