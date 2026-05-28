"""End-to-end coverage for `transition done` under a failed merge —
the HATS-481/541 silent-DONE protection PLUS the HATS-587/F5 clean-retry.

HATS-481 fixed the first-attempt swallow: a merge failure must NOT
silently stamp the task DONE. HATS-541 added a defensive guard for the
orphan that a failed merge USED to leave behind (worktree dir + state
cleared, branch preserved → second `transition done` silently no-oped).

HATS-587 / F5 removed that orphan at the source: a failed merge now
PRESERVES the worktree dir + branch + state, so the second
`transition done` (after the operator resolves the cause) is a clean
RETRY that succeeds — no manual `git merge --no-ff` recovery. The
WorktreeStateLostError guard survives as defense-in-depth for residual
orphan causes (manual deletion, crash on the success path); it is
exercised at the unit level in
`tests/test_state.py::test_teardown_worktree_raises_when_state_lost_but_branch_exists`.

Per `dev_rule_e2e_gate`: HATS-587 touches `src/ai_hats/cli/` +
`src/ai_hats/worktree.py`, so a real-launcher + real-binary e2e is
mandatory. CliRunner / pipeline tests do NOT satisfy the gate.

**Fail-under-revert** (HATS-587/F5): restore the
`self._remove_worktree()` + `self._clear_state()` calls in
`WorktreeManager.merge`'s `except` block. Attempt 2 then finds the state
gone, the merge never re-runs, and step (7) observes the task stuck in
`review` (WorktreeStateLostError) instead of advancing to `done` — the
final assertion fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


def _task_state(project: Path, task_id: str) -> str:
    yaml_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
        / task_id / "task.yaml"
    )
    text = yaml_path.read_text()
    for line in text.splitlines():
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no state field in {yaml_path}:\n{text}")


@pytest.mark.integration
def test_e2e_failed_done_stays_review_then_retry_succeeds(tmp_path):
    """Two-attempt `transition done` flow under a forced merge conflict.

    Attempt 1: merge conflict → exit non-zero, task stays in `review`,
    and the worktree + branch are PRESERVED (HATS-587/F5).
    Attempt 2: after the operator resolves the collision, the worktree is
    still present, so `transition done` re-runs the merge cleanly and the
    task advances to `done`.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    # ---- install launcher ----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    assert launcher_dest.is_file()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("init\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "update")
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- create task + walk plan→execute ----
    task_id = "TST-001"
    branch_ref = f"task/{task_id.lower()}"
    ai_hats(
        "task", "create", "Failed-merge retry regression",
        "-d", "Used to verify HATS-481/541 silent-done + HATS-587/F5 retry.",
        "--id", task_id,
    )
    ai_hats("task", "transition", task_id, "plan")

    plans_dir = project / ".claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / "001-conflict.md"
    plan_path.write_text(
        f"# {task_id} plan\n\nWrite to COLLIDE.txt and try to merge twice.\n"
    )
    ai_hats("task", "plan-sync", task_id, "--from-file", str(plan_path))

    ai_hats("task", "transition", task_id, "execute")

    # ---- locate worktree (reuses test_wt_merge_drift.py pattern) ----
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith(f"/{branch_ref}"):
                wt_path = current
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree for {branch_ref}:\n{listing}"
    )

    # ---- engineer the conflict (HATS-529 reproduction) ----
    # Worktree side: commit a NEW file. Drift check on attempt 1
    # passes because master HEAD doesn't move below.
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "COLLIDE.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "COLLIDE.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "worktree adds COLLIDE.txt",
    )

    # Main side: place an UNTRACKED file at the same path. Drift check
    # is satisfied (no commits on main since worktree create). But
    # `git merge --no-ff` exits 2 with "untracked working tree files
    # would be overwritten by merge" — exactly the failure that
    # orphaned a branch in the originating session.
    (project / "COLLIDE.txt").write_text("untracked-on-main\n")
    # Intentionally NOT staged / committed.

    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- Attempt 1: must exit non-zero, state must stay `review`. ----
    res1 = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=None, timeout=90,
    )
    assert res1.returncode != 0, (
        f"attempt 1: transition done exited 0 despite merge conflict\n"
        f"stdout:\n{res1.stdout}\nstderr:\n{res1.stderr}"
    )
    assert _task_state(project, task_id) == "review", (
        "attempt 1: task moved out of `review` despite merge failure"
    )

    # ---- HATS-587/F5: worktree dir + branch PRESERVED on failure. ----
    branches = _git(project, "branch", "--list", branch_ref).stdout
    assert branch_ref in branches, (
        f"attempt 1: worktree branch must be preserved:\n{branches}"
    )
    assert wt_path.is_dir(), (
        "🐛 F5 REGRESSION: a failed merge tore down the worktree directory — "
        "the next `transition done` can no longer be a clean retry"
    )

    # ---- Resolve the underlying untracked-file collision. ----
    # Mirrors what an operator does between attempts; also abort any stray
    # MERGING state left by the conflicting merge.
    (project / "COLLIDE.txt").unlink()
    subprocess.run(
        ["git", "merge", "--abort"], cwd=str(project),
        capture_output=True, check=False,
    )

    # ---- Attempt 2: clean retry → task advances to `done`. ----
    res2 = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=None, timeout=90,
    )
    assert res2.returncode == 0, (
        f"🐛 attempt 2 should be a clean retry now that the worktree is "
        f"preserved (HATS-587/F5)\n"
        f"stdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
    )

    # ---- Task state must now be `done`, with a real merge behind it. ----
    assert _task_state(project, task_id) == "done", (
        f"attempt 2: task should advance to `done` after a clean retry\n"
        f"stdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
    )
    # The worktree commit actually landed on the base branch.
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "worktree adds COLLIDE.txt" in log, (
        f"worktree commit missing from history — merge did not really "
        f"happen:\n{log}"
    )
    # Worktree branch cleaned up after the successful merge.
    branches_final = _git(project, "branch", "--list", branch_ref).stdout
    assert branches_final.strip() == "", (
        f"worktree branch should be deleted after the successful retry:\n"
        f"{branches_final!r}"
    )
