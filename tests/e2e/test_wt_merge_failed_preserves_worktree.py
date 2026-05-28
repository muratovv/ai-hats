"""End-to-end coverage for ``ai-hats wt merge`` failure-teardown ordering
(HATS-587 / F5).

Pre-587 a failed merge tore down the worktree dir + cleared state.json in
the ``except`` block, leaving an orphaned branch with no worktree — recovery
required a manual ``git merge --no-ff``. F5 moves teardown to AFTER the merge
commit succeeds: a failed merge now leaves the worktree dir + branch + state
fully intact, so the next ``wt merge`` is a clean retry once the operator
resolves the cause.

Per ``dev_rule_e2e_gate``: change to ``src/ai_hats/worktree.py`` (+ CLI)
requires a real-launcher + real-binary e2e. CliRunner / pipeline tests do
NOT satisfy the gate.

**Fail-under-revert**: restore the ``self._remove_worktree()`` +
``self._clear_state()`` calls in ``WorktreeManager.merge``'s ``except``
block → step (5) below finds the worktree dir gone and the assertion fails.

The conflict is engineered via an UNTRACKED-file collision (not a base-side
commit) so the drift guard does NOT pre-empt: the base branch HEAD never
moves, so ``_check_drift`` passes and ``git merge --no-ff`` actually runs and
fails on "untracked working tree files would be overwritten" — the exact
shape that orphaned a branch in the originating session.

Modelled on ``tests/e2e/test_wt_merge_head_wandered.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


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
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/preserve-probe")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith("/task/preserve-probe"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path:\n{listing}"
    )

    # ---- 3. worktree branch commits a NEW file ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "COLLIDE.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "COLLIDE.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "worktree adds COLLIDE.txt",
    )

    # ---- 4. untracked collision on main (no commit → no drift) ----
    (project / "COLLIDE.txt").write_text("untracked-on-main\n")

    # ---- 5. wt merge fails, worktree + branch PRESERVED ----
    res = ai_hats(
        "wt", "merge", "task/preserve-probe",
        expect_exit=None, cwd=project,
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
        f"worktree branch should be deleted after a successful retry:\n"
        f"{branches!r}"
    )
    assert not wt_path.is_dir(), (
        "worktree directory should be gone after a successful merge"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "worktree adds COLLIDE.txt" in log, (
        f"worktree commit not in history after retry:\n{log}"
    )
