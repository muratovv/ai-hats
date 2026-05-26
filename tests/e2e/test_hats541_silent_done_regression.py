"""End-to-end coverage for HATS-541 — `transition done` must NOT
silently mark a task DONE on a SECOND attempt after the first attempt's
merge failed and orphaned the worktree state.

Sibling to ``test_wt_merge_conflict_preserves_review.py`` (HATS-481).
HATS-481 fixed the FIRST-attempt swallow. HATS-541 fixes the
SECOND-attempt silent-no-op: after the first failure, ``Worktree.merge()``
clears ``state.json`` AND removes the worktree dir but PRESERVES the
branch. ``WorktreeManager.load_for_task`` then returns ``None`` and the
pre-541 ``_teardown_worktree`` silently returned → ``_save_task``
stamped DONE without any merge.

Per ``dev_rule_e2e_gate``: the change touches ``state.py`` + adds a new
exception type, NOT ``cli/`` / ``scripts/`` / ``_bootstrap.py``. The
gate doesn't strictly require an e2e — but the bug WAS reproduced via
the CLI binary in the originating session, so this e2e is the strongest
fail-under-revert and the only test that catches a CLI-handler
regression.

**Fail-under-revert** (mandatory): remove the ``WorktreeStateLostError``
raise in ``state.py:_teardown_worktree``'s ``active is None`` branch.
Step (8) below observes ``state == "done"`` instead of ``"review"`` and
the assertion fails. Verified locally before commit.
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
def test_e2e_second_done_attempt_after_failed_merge_does_not_silently_succeed(tmp_path):
    """Two-attempt `transition done` flow under a forced merge conflict.

    Attempt 1: merge conflict → exit non-zero, task stays in `review`.
    Attempt 2: state.json + worktree dir are gone (cleared by attempt
    1's `Worktree.merge()` failure path), but the branch is preserved.
    The HATS-541 defensive raise MUST fire — exit non-zero, task STILL
    in `review`, recovery hint surfaced.
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
        "task", "create", "Silent-done regression",
        "-d", "Used to verify HATS-541 defensive raise on retry.",
        "--id", task_id,
    )
    ai_hats("task", "transition", task_id, "plan")

    plans_dir = project / ".claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / "001-conflict.md"
    plan_path.write_text(
        f"# {task_id} plan\n\nWrite to CONFLICT.txt and try to merge twice.\n"
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
    (wt_path / "ADDED.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "ADDED.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "worktree adds ADDED.txt",
    )

    # Main side: place an UNTRACKED file at the same path. Drift check
    # is satisfied (no commits on main since worktree create). But
    # `git merge --no-ff` exits 2 with "untracked working tree files
    # would be overwritten by merge" — exactly the failure that
    # triggered HATS-541 in the originating session.
    (project / "ADDED.txt").write_text("untracked-on-main\n")
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

    # ---- Sanity: worktree branch preserved, state.json gone. ----
    branches = _git(project, "branch", "--list", branch_ref).stdout
    assert branch_ref in branches, (
        f"attempt 1: worktree branch missing — repro premise broken:\n"
        f"{branches}"
    )

    # ---- Resolve the underlying untracked-file collision ----
    # (the original HATS-529 recovery user action). Mirrors what an
    # operator would do between attempts. Also abort any stray MERGING
    # state defensively.
    (project / "ADDED.txt").unlink()
    subprocess.run(
        ["git", "merge", "--abort"], cwd=str(project),
        capture_output=True, check=False,
    )

    # ---- Attempt 2: the HATS-541 defensive raise MUST fire. ----
    res2 = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=None, timeout=90,
    )
    assert res2.returncode != 0, (
        f"🐛 HATS-541 REGRESSION: attempt 2 silently succeeded despite "
        f"orphaned worktree branch.\n"
        f"stdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
    )
    combined = res2.stdout + res2.stderr
    assert "worktree state lost" in combined.lower(), (
        f"attempt 2: refusal banner missing\n{combined}"
    )
    assert branch_ref in combined, (
        f"attempt 2: branch name missing from recovery hint\n{combined}"
    )
    assert "git merge --no-ff" in combined, (
        f"attempt 2: manual recovery recipe missing\n{combined}"
    )

    # ---- Task state must STILL be `review`. ----
    assert _task_state(project, task_id) == "review", (
        "🐛 HATS-541 REGRESSION: task moved to DONE on attempt 2 "
        "without a real merge — silent-data-loss class."
    )

    # ---- Branch still preserved for actual manual recovery. ----
    branches_final = _git(project, "branch", "--list", branch_ref).stdout
    assert branch_ref in branches_final, (
        f"attempt 2: branch must remain preserved across the refusal\n"
        f"{branches_final}"
    )
