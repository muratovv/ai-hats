"""End-to-end coverage for HATS-481 L1' + L3' — parallel `ai-hats task
transition <ID> done` on tasks sharing a base ref must both land cleanly.

Per ``dev_rule_e2e_gate`` (and the precedent of
``tests/e2e/test_wt_merge_drift.py`` / ``test_wt_merge_conflict_preserves_review.py``):
user-visible behavior of ``ai-hats task transition <ID> done`` requires
a real-binary e2e test.

The bug we are preventing: two ``transition done`` invocations on
worktrees sharing a base ref both run ``git merge --no-ff <task-branch>``
on the base. Git's ``.git/index.lock`` rejects the second → CalledProcessError.
Pre-HATS-481 ``_teardown_worktree`` swallowed it and ``transition``
still marked the task DONE (silent data loss).

With L4' alone the loser exits non-zero and the user retries.
With L1' (base-branch lock) + L3' (retry) the contention is invisible
to the user and both transitions complete first time.

**Fail-under-revert** (mandatory per e2e gate):
disabling BOTH L1' (replace `_acquire_base_branch_lock` body with
``contextlib.nullcontext``) AND L3' (set ``MERGE_RETRY_MAX=1``) makes
this test fail — `index.lock` contention causes one transition to exit
non-zero. Disabling either layer alone is non-deterministic:

==============  ===========================  ===========================
Revert          What remains active           Result
==============  ===========================  ===========================
Only L1'        L3' + L4'                     L3' may absorb → flaky
Only L4'        L1' + L3'                     L1' serializes → green
Only L3'        L1' + L4'                     L1' serializes → green
**L1' + L3'**   Only L4'                      Loser exits 1 → fails ✓
==============  ===========================  ===========================

The single test in this file therefore verifies the *interaction* of
L1' + L3'; ``test_wt_merge_conflict_preserves_review.py`` (TC-E2)
verifies L4' in isolation. Together they cover the whole stack.
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
    yaml_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
        / task_id / "task.yaml"
    )
    text = yaml_path.read_text()
    for line in text.splitlines():
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no state field in {yaml_path}:\n{text}")


def _walk_task_to_review(
    ai_hats, project: Path, task_id: str, plans_dir: Path,
    payload_file: str, payload_content: str,
) -> None:
    """plan → execute → write commit in worktree → document → review."""
    ai_hats("task", "transition", task_id, "plan")

    plan_path = plans_dir / f"{task_id.lower()}.md"
    plan_path.write_text(
        f"# {task_id} plan\n\nWrite to {payload_file} and merge into base.\n"
    )
    ai_hats(
        "task", "plan-sync", task_id, "--from-file", str(plan_path),
    )
    ai_hats("task", "transition", task_id, "execute")

    # Locate the worktree.
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
        f"no worktree found for {branch_ref}:\n{listing}"
    )

    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / payload_file).write_text(payload_content)
    _git(wt_path, "add", payload_file)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "commit", "-m", f"add {payload_file}"],
        cwd=str(wt_path), check=True,
        capture_output=True, text=True,
    )
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")


@pytest.mark.integration
def test_e2e_parallel_transition_done_no_data_loss(shared_launcher, tmp_path):
    """Two `transition done` on tasks sharing a base ref both succeed
    cleanly under L1' + L3' — no silent data loss, no flake."""
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap ----
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

    plans_dir = project / ".claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # ---- two tasks, both rooted in `main` ----
    task_a, task_b = "TST-001", "TST-002"
    ai_hats("task", "create", "Task A", "-d", "First", "--id", task_a)
    ai_hats("task", "create", "Task B", "-d", "Second", "--id", task_b)
    _walk_task_to_review(ai_hats, project, task_a, plans_dir, "file-a.txt", "alpha\n")
    _walk_task_to_review(ai_hats, project, task_b, plans_dir, "file-b.txt", "beta\n")

    # Capture HEAD before the race for the merge-count assertion.
    head_before = _git(project, "rev-parse", "HEAD").stdout.strip()

    # ---- the race ----
    cmd_a = [str(launcher_dest), "task", "transition", task_a, "done"]
    cmd_b = [str(launcher_dest), "task", "transition", task_b, "done"]
    p1 = subprocess.Popen(
        cmd_a, cwd=str(project), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    p2 = subprocess.Popen(
        cmd_b, cwd=str(project), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out1, err1 = p1.communicate(timeout=90)
    out2, err2 = p2.communicate(timeout=90)

    assert p1.returncode == 0 and p2.returncode == 0, (
        f"parallel transition done failed under L1'+L3':\n"
        f"task_a exit={p1.returncode}\nstdout:{out1}\nstderr:{err1}\n"
        f"task_b exit={p2.returncode}\nstdout:{out2}\nstderr:{err2}"
    )

    # ---- both unique files must be in the base branch ----
    assert (project / "file-a.txt").read_text() == "alpha\n", (
        "task A's commit missing from base — silent data loss"
    )
    assert (project / "file-b.txt").read_text() == "beta\n", (
        "task B's commit missing from base — silent data loss"
    )

    # ---- both tasks must be marked DONE on disk ----
    assert _task_state(project, task_a) == "done"
    assert _task_state(project, task_b) == "done"

    # ---- exactly 2 merge commits since head_before ----
    log = _git(
        project, "log", "--merges", "--pretty=%H",
        f"{head_before}..HEAD",
    ).stdout.strip().splitlines()
    assert len(log) == 2, (
        f"expected 2 merge commits since base, got {len(log)}:\n{log}"
    )
