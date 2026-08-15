"""e2e (HATS-481)

flow:   two developer processes concurrently running transition done on tasks sharing
        base branch
cmds:
    rack transition TST-001 done
expect: base branch lock serializes merges and both transitions succeed without data
        loss
why:    concurrent task finalization must synchronize base branch merges to prevent lock
        contention"""

from __future__ import annotations
from _helpers.env import consented
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest


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
    yaml_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "task.yaml"
    )
    text = yaml_path.read_text()
    for line in text.splitlines():
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no state field in {yaml_path}:\n{text}")


def _walk_task_to_review(
    rack,
    project: Path,
    task_id: str,
    payload_file: str,
    payload_content: str,
) -> None:
    """plan → execute → write commit in worktree → document → review."""
    rack("transition", task_id, "plan")

    # Plan content goes straight into the canonical task tree — no
    # .claude/plans round-trip (HATS-637).
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        f"# {task_id} plan\n\n"
        f"## Requirements\nWrite to {payload_file} and merge into base.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] write\n\n"
        "## Verification Protocol\nmerge\n"
    )
    rack("transition", task_id, "execute")

    # Locate the worktree.
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
        f"no worktree found for {branch_ref}:\n{listing}"
    )

    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / payload_file).write_text(payload_content)
    _git(wt_path, "add", payload_file)
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            f"add {payload_file}",
        ],
        cwd=str(wt_path),
        check=True,
        capture_output=True,
        text=True,
    )
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")


@pytest.mark.integration
def test_e2e_parallel_transition_done_no_data_loss(shared_launcher, tmp_path):
    """Two `transition done` on tasks sharing a base ref both succeed
    cleanly under L1' + L3' — no silent data loss, no flake."""
    launcher_dest, base_env, venv = shared_launcher
    rack_bin = venv / "bin" / "rack"
    # plan → execute is consent-gated; the gate is not this test's subject.
    env = {**base_env, "AI_HATS_PLAN_ACK": "1"}
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

    def rack(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(rack_bin), *args],
            cwd=cwd,
            env=env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- bootstrap ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
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

    # ---- two tasks, both rooted in `main` ----
    task_a, task_b = "TST-001", "TST-002"
    rack("create", "Task A", "--description", "First", "--id", task_a)
    rack("create", "Task B", "--description", "Second", "--id", task_b)
    _walk_task_to_review(rack, project, task_a, "file-a.txt", "alpha\n")
    _walk_task_to_review(rack, project, task_b, "file-b.txt", "beta\n")

    # Capture HEAD before the race for the merge-count assertion.
    head_before = _git(project, "rev-parse", "HEAD").stdout.strip()

    # ---- the race ----
    # HATS-1682: `review -> done` is a declared consent point. Both racers
    # carry the answer — the subject is the base-ref lock, and an edge that
    # refuses for want of a click never reaches it.
    done_env = consented(env)
    cmd_a = [str(rack_bin), "transition", task_a, "done"]
    cmd_b = [str(rack_bin), "transition", task_b, "done"]
    p1 = subprocess.Popen(
        cmd_a,
        cwd=str(project),
        env=done_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        cmd_b,
        cwd=str(project),
        env=done_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    log = (
        _git(
            project,
            "log",
            "--merges",
            "--pretty=%H",
            f"{head_before}..HEAD",
        )
        .stdout.strip()
        .splitlines()
    )
    assert len(log) == 2, f"expected 2 merge commits since base, got {len(log)}:\n{log}"
