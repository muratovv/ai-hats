"""e2e (HATS-481, HATS-1651)

flow:   a developer finalizing a task whose branch diverged from the base
cmds:
    rack transition TST-001 done
expect: the task stays in review and the worktree branch survives for resolution
why:    a task must not reach done when the merge it needs cannot be performed
"""

# HATS-1651 corrected the claim the block above used to make ("when a git merge
# conflict occurs"). Conflicting content means the base moved, which IS drift, and
# this road merges with accept_drift=False (wt_effects.py) — so the refusal comes
# from the drift guard BEFORE any `git merge` runs, and a conflict is unreachable
# here. The assertions below are true and worth keeping; they were simply never
# about a conflict, which is how the crash HATS-1651 fixes stayed uncovered. The
# conflict needs --accept-drift and lives in test_wt_merge_conflict_is_atomic.py.
# comment-length: allow — the corrected coverage claim IS the point

from __future__ import annotations
from _helpers.git import git as _git

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


def _task_state(project: Path, task_id: str) -> str:
    """Read state field from on-disk task.yaml — no CLI involved."""
    yaml_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "task.yaml"
    )
    text = yaml_path.read_text()
    for line in text.splitlines():
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no state field in {yaml_path}:\n{text}")


@pytest.mark.integration
def test_e2e_merge_conflict_does_not_mark_task_done(shared_launcher, tmp_path):
    """A `transition done` whose merge is refused must leave the task in
    `review` (not DONE) and preserve the worktree branch for retry."""
    launcher_dest, env, venv = shared_launcher
    rack_bin = venv / "bin" / "rack"
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

    def rack(*args, expect_exit=0, timeout=180, cwd=project, extra_env=None):
        return _run(
            [str(rack_bin), *args],
            cwd=cwd,
            env={**env, **(extra_env or {})},
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "CONFLICT.txt").write_text("v1\n")
    _git(project, "add", "CONFLICT.txt")
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

    # ---- create task + walk plan→execute ----
    task_id = "TST-001"
    rack(
        "create",
        "Conflict test",
        "--description",
        "Used to verify HATS-481 L4'.",
        "--id",
        task_id,
    )
    rack("transition", task_id, "plan")

    # A real plan is required (the empty scaffold is rejected by
    # strict_plan_check on transition execute). Write it straight into the
    # canonical task tree — no .claude/plans round-trip (HATS-637).
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        "# TST-001 plan\n\n"
        "## Requirements\nWrite to CONFLICT.txt and try to merge.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] write\n\n"
        "## Verification Protocol\nmerge\n"
    )

    # plan → execute is consent-gated on rack; the launcher env carries no ack.
    rack("transition", task_id, "execute", extra_env={"AI_HATS_PLAN_ACK": "1"})

    # ---- locate worktree (same pattern as test_wt_merge_drift.py) ----
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current: Path | None = None
    branch_ref = f"task/{task_id.lower()}"
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch ") :].strip()
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
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "worktree change",
    )

    # Main side: change CONFLICT.txt to "from-master" — irreconcilable
    # with the worktree change. `git merge --no-ff task/tst-001` MUST
    # leave the index in a conflicted state and exit non-zero.
    (project / "CONFLICT.txt").write_text("from-master\n")
    _git(project, "add", "CONFLICT.txt")
    _git(
        project,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "master change",
    )

    # ---- walk task to review ----
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- the contended transition done — MUST exit non-zero ----
    res = rack(
        "transition",
        task_id,
        "done",
        expect_exit=None,
        timeout=90,
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
        "task moved out of `review` despite merge failure — silent data loss regression"
    )

    # ---- worktree branch preserved for retry ----
    branches = _git(project, "branch", "--list", branch_ref).stdout
    assert branch_ref in branches, f"worktree branch must be preserved for retry:\n{branches}"
