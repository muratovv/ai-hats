"""End-to-end coverage for HATS-481 L4' — `transition done` must NOT
mark a task DONE when the merge fails (e.g. merge conflict).

Per ``dev_rule_e2e_gate`` (and the precedent of
``tests/e2e/test_wt_merge_drift.py``): user-visible behavior of
``ai-hats task transition <ID> done`` requires a real-binary e2e test.

The bug pre-HATS-481: `_teardown_worktree` caught ALL exceptions at
WARNING and let `transition` proceed to `_save_task`, persisting the
new DONE state despite the merge failure. Same class as the GitHub
Merge Queue Apr-2026 incident — work silently dropped, state lies.

**Fail-under-revert** (mandatory): replace the new fail-loud block in
`_teardown_worktree` with the pre-HATS-481 `except Exception:
logger.warning(...)` swallow. With L4' reverted, step (7) below
observes ``state == "done"`` instead of ``"review"`` and the assertion
fails. Verified locally before commit.
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
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


def _task_state(project: Path, task_id: str) -> str:
    """Read state field from on-disk task.yaml — no CLI involved."""
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
def test_e2e_merge_conflict_does_not_mark_task_done(shared_launcher, tmp_path):
    """Forcing a merge conflict on `transition done` must leave the task
    in `review` (not DONE) and preserve the worktree branch for retry."""
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "CONFLICT.txt").write_text("v1\n")
    _git(project, "add", "CONFLICT.txt")
    _git(project, "commit", "-m", "init")

    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- create task + walk plan→execute ----
    task_id = "TST-001"
    ai_hats(
        "task", "create", "Conflict test",
        "-d", "Used to verify HATS-481 L4'.",
        "--id", task_id,
    )
    ai_hats("task", "transition", task_id, "plan")

    # Plan-sync requires a real plan file (the scaffold is rejected by
    # strict_plan_check on transition execute). Write a minimal plan into
    # .claude/plans/ and let plan-sync ingest it.
    plans_dir = project / ".claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / "001-conflict.md"
    plan_path.write_text(
        "# TST-001 plan\n\nWrite to CONFLICT.txt and try to merge.\n"
    )
    ai_hats("task", "plan-sync", task_id, "--from-file", str(plan_path))

    ai_hats("task", "transition", task_id, "execute")

    # ---- locate worktree (same pattern as test_wt_merge_drift.py) ----
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current: Path | None = None
    branch_ref = f"task/{task_id.lower()}"
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

    # ---- engineer the conflict ----
    # Worktree side: change CONFLICT.txt to "from-worktree".
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "CONFLICT.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "CONFLICT.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "worktree change",
    )

    # Main side: change CONFLICT.txt to "from-master" — irreconcilable
    # with the worktree change. `git merge --no-ff task/tst-001` MUST
    # leave the index in a conflicted state and exit non-zero.
    (project / "CONFLICT.txt").write_text("from-master\n")
    _git(project, "add", "CONFLICT.txt")
    _git(
        project, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "master change",
    )

    # ---- walk task to review ----
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- the contended transition done — MUST exit non-zero ----
    res = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=None, timeout=90,
    )
    assert res.returncode != 0, (
        f"transition done exited 0 despite merge conflict — "
        f"silent data loss regression\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    combined = (res.stdout + res.stderr).lower()
    assert "traceback" not in combined or "merge" in combined, (
        f"no merge mention in failure output:\n{res.stdout}\n{res.stderr}"
    )

    # ---- on-disk task state must remain `review` ----
    assert _task_state(project, task_id) == "review", (
        "task moved out of `review` despite merge failure — "
        "silent data loss regression"
    )

    # ---- worktree branch preserved for retry ----
    branches = _git(project, "branch", "--list", branch_ref).stdout
    assert branch_ref in branches, (
        f"worktree branch must be preserved for retry:\n{branches}"
    )
