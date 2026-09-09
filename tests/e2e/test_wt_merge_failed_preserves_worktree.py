"""e2e (HATS-587)

flow:   a developer merging a worktree branch when git merge fails due to untracked file
        collision
cmds:
    # when untracked file collision causes merge failure
    ai-hats wt merge task/preserve-probe
expect: worktree directory and branch are preserved intact for retry after resolving
        failure
why:    failed merges must not tear down worktree state to allow clean operator recovery
"""

from __future__ import annotations
from _helpers.git import init_repo, git as _git

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_wt_merge_failure_preserves_worktree(shared_launcher, tmp_path):
    """HATS-587 / F5: a failed ``wt merge`` leaves the worktree dir + branch
    intact, and a retry after resolving the cause succeeds. Real subprocess.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create
         task/preserve-probe`` from the default branch.
      3. Worktree: commit a NEW file ``COLLIDE.txt``.
      4. Main repo: place an UNTRACKED ``COLLIDE.txt`` at the same path
         (no commit → no drift). The merge will refuse to overwrite it.
      5. ``ai-hats wt merge task/preserve-probe`` MUST exit non-zero, and
         the worktree dir + branch MUST still exist (F5 — no teardown on
         failure).
      6. Resolve the collision (remove the untracked file), retry
         ``wt merge`` → succeeds; worktree branch gone, commit on base.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd,
            env=env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
    init_repo(project, branch="main")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "claude",
        "--task-prefix",
        "TST",
    )

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/preserve-probe")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch ") :].strip()
            if ref.endswith("/task/preserve-probe"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), f"could not locate worktree path:\n{listing}"

    # ---- 3. worktree branch commits a NEW file ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "COLLIDE.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "COLLIDE.txt")
    _git(
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "worktree adds COLLIDE.txt",
    )

    # ---- 4. untracked collision on main (no commit → no drift) ----
    (project / "COLLIDE.txt").write_text("untracked-on-main\n")

    # ---- 5. wt merge fails, worktree + branch PRESERVED ----
    res = ai_hats(
        "wt",
        "merge",
        "task/preserve-probe",
        expect_exit=None,
        cwd=project,
    )
    assert res.returncode != 0, (
        f"wt merge unexpectedly succeeded despite the untracked collision\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert wt_path.is_dir(), (
        "🐛 F5 REGRESSION: a failed merge tore down the worktree directory — "
        "this orphans the branch and forces manual recovery"
    )
    branches = _git(project, "branch", "--list", "task/preserve-probe").stdout
    assert "task/preserve-probe" in branches, (
        f"worktree branch must be preserved after a failed merge:\n{branches}"
    )
    # The worktree is still tracked by git (admin entry intact).
    wt_listing = _git(project, "worktree", "list", "--porcelain").stdout
    assert str(wt_path) in wt_listing, (
        f"worktree admin entry must survive a failed merge:\n{wt_listing}"
    )

    # ---- 6. resolve + retry → clean success ----
    (project / "COLLIDE.txt").unlink()
    ai_hats("wt", "merge", "task/preserve-probe", cwd=project)
    branches = _git(project, "branch", "--list", "task/preserve-probe").stdout
    assert branches.strip() == "", (
        f"worktree branch should be deleted after a successful retry:\n{branches!r}"
    )
    assert not wt_path.is_dir(), "worktree directory should be gone after a successful merge"
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "worktree adds COLLIDE.txt" in log, f"worktree commit not in history after retry:\n{log}"
