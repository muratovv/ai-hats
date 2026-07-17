"""End-to-end coverage for the merge consent gate (HATS-1019).

Per ``dev_rule_e2e_gate``: touches ``src/ai_hats/cli/worktree.py`` and
``packages/ai-hats-wt/src/ai_hats_wt/manager.py`` → real launcher + real
``ai-hats`` binary. ``wt merge`` is default-deny without
``AI_HATS_MERGE_ACK=1`` — review must actually reach the supervisor
before a task branch is published to the base.

**Fail-under-revert**: comment out the consent check in
``WorktreeManager.merge`` → step (4) merges with exit 0 instead of the
directive refusal, and the test fails.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


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
def test_e2e_wt_merge_consent_gate(shared_launcher, tmp_path):
    """Deny without ack (directive message, branch preserved) → ack merges.

    Scenario:
      1. Bootstrap: git repo + ``self init``.
      2. ``wt create task/test-consent`` + a commit on the task branch.
      3. ``wt merge`` WITHOUT ``AI_HATS_MERGE_ACK`` — exit 1, message names
         the env var and the review handoff, branch + worktree preserved.
      4. ``wt merge`` with ``AI_HATS_MERGE_ACK=1`` — exit 0, work lands.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    ack_env = dict(env)  # shared_launcher env grants ack by default
    deny_env = dict(env)
    deny_env.pop("AI_HATS_MERGE_ACK", None)

    def ai_hats(*args, expect_exit=0, timeout=180, run_env=ack_env):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=run_env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")

    # ---- 2. worktree + task-branch commit ----
    ai_hats("wt", "create", "task/test-consent")
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch "):].strip().endswith("/task/test-consent"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path:\n{listing}"
    )

    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )

    # ---- 3. merge without ack: directive refusal ----
    res = ai_hats(
        "wt", "merge", "task/test-consent",
        expect_exit=1, run_env=deny_env,
    )
    combined = res.stdout + res.stderr
    assert "AI_HATS_MERGE_ACK" in combined, (
        f"consent env var not named in refusal:\n{combined}"
    )
    assert "review" in combined.lower(), (
        f"review handoff directive missing from refusal:\n{combined}"
    )

    branches = _git(project, "branch", "--list", "task/test-consent").stdout
    assert "task/test-consent" in branches, (
        f"consent refusal must preserve the task branch:\n{branches}"
    )
    assert wt_path.is_dir(), "consent refusal must preserve the worktree dir"
    assert not (project / "wt-work.txt").exists(), "base must be untouched"

    # ---- 4. merge with ack lands the work ----
    merge_res = ai_hats("wt", "merge", "task/test-consent", run_env=ack_env)

    branches = _git(project, "branch", "--list", "task/test-consent").stdout
    assert branches.strip() == "", (
        f"task branch should be deleted after merge:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history:\n{log}"


@pytest.mark.integration
def test_e2e_transition_done_inner_merge_denied(shared_launcher, tmp_path):
    """The canonical supervised close, end to end.

    Agent side runs WITHOUT ack throughout:
      create → plan → execute → commit → document → review → ``done`` is
      REFUSED (directive, card stays in review, branch preserved).
    Supervisor merges with ack; the agent's retried ack-free ``done``
    then passes via the already-merged short-circuit (HATS-596).
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    ack_env = dict(env)
    deny_env = dict(env)
    deny_env.pop("AI_HATS_MERGE_ACK", None)

    def ai_hats(*args, expect_exit=0, timeout=180, run_env=deny_env):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=run_env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap + task → execute ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")

    new_res = ai_hats(
        "task", "create", "consent gate test",
        "--description", "exercise the HATS-1019 supervised close",
        "--role", "assistant", "--reviewer", "user",
    )
    task_id = None
    for line in new_res.stdout.splitlines():
        if line.strip().startswith("Created:"):
            task_id = line.split()[1]
            break
    assert task_id and task_id.startswith("TST-"), (
        f"could not parse task ID from:\n{new_res.stdout}"
    )

    ai_hats("task", "transition", task_id, "plan")
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise the consent gate.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    ai_hats("task", "transition", task_id, "execute")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    task_branch = f"task/{task_id.lower()}"
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch "):].strip().endswith(f"/{task_branch}"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path for {task_id}:\n{listing}"
    )

    # ---- 2. commit work, walk to review ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- 3. agent's `done` without ack: directive refusal, review intact ----
    res = ai_hats("task", "transition", task_id, "done", expect_exit=1)
    combined = res.stdout + res.stderr
    assert "AI_HATS_MERGE_ACK" in combined, (
        f"consent env var not named in refusal:\n{combined}"
    )
    assert "review" in combined.lower(), (
        f"review handoff directive missing:\n{combined}"
    )
    branches = _git(project, "branch", "--list", task_branch).stdout
    assert task_branch in branches, "refusal must preserve the task branch"
    show = ai_hats("task", "show", task_id).stdout
    assert "review" in show, f"card must stay in review after refusal:\n{show}"

    # ---- 4. supervisor merges with ack; agent's ack-free retry closes ----
    ai_hats("wt", "merge", task_branch, run_env=ack_env)
    ai_hats("task", "transition", task_id, "done", run_env=deny_env)

    log = _git(project, "log", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history:\n{log}"
    branches = _git(project, "branch", "--list", task_branch).stdout
    assert branches.strip() == "", "task branch should be gone after close"
