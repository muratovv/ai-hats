"""e2e (HATS-518, HATS-1263)

flow:   a developer creating a worktree or executing a task from a feature branch
cmds:
    # when checked out on a feature branch
    ai-hats wt create task/probe
expect: worktree creation and task execution are refused when main repository HEAD is
        not on base
why:    worktrees must be created from base branch to prevent branching off dirty
        feature branches
"""

from __future__ import annotations
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _rack(proj, *args: str) -> subprocess.CompletedProcess[str]:
    """The venv's real ``rack`` console script (HATS-1263)."""
    import os

    from ai_hats.paths import ENV_AI_HATS_VENV

    from _helpers.env import clean_env

    env = clean_env(os.environ)
    env.update(proj.env)
    env["AI_HATS_PLAN_ACK"] = "1"  # else plan->execute stops at the consent gate
    rack_bin = Path(proj.env[ENV_AI_HATS_VENV]) / "bin" / "rack"
    return subprocess.run(
        [str(rack_bin), *args],
        cwd=str(proj.path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _ok(res: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    assert res.returncode == 0, f"{res.args}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    return res


def _created_id(res: subprocess.CompletedProcess[str]) -> str:
    """Parse `Created: <ID> …` — the default prefix differs between the CLIs
    (legacy TASK, rack HATS), so ids are never hardcoded."""
    for line in res.stdout.splitlines():
        if line.strip().startswith("Created:"):
            return line.split()[1]
    raise AssertionError(f"no Created: line in:\n{res.stdout}")


def _git_init_on_master(project: Path) -> None:
    """git init -b master + one commit + .agent layout for _project_dir()."""
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    (project / ".agent").mkdir(exist_ok=True)
    (project / ".agent" / "STATE.md").write_text("")
    (project / ".agent" / "backlog").mkdir(parents=True, exist_ok=True)
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def test_wt_create_refuses_on_feature_branch(tmp_venv_project) -> None:
    """Full e2e: real binary on master vs feature branch.

    1. ``git init -b master`` + commit + .agent layout.
    2. ``git checkout -b feat/parking``.
    3. ``ai-hats wt create task/probe`` → exit 1, red "Refused", message
       names both ``feat/parking`` and ``master``.
    4. ``git worktree list`` shows only the main worktree (no leak).
    5. ``git checkout master``.
    6. ``ai-hats wt create task/probe`` → exit 0, ``Worktree created``,
       ``git worktree list`` now shows two entries.
    """
    proj = tmp_venv_project
    project = proj.path

    _git_init_on_master(project)
    _git(project, "checkout", "-b", "feat/parking")

    # --- Refusal path ---
    result = proj.run("wt", "create", "task/probe")
    (result.expect_failure().expect_stdout_contains("Refused", "feat/parking", "master"))

    # No leaked worktree directory: only the main repo's worktree should
    # be listed. `git worktree list` first line is the main worktree.
    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 1, (
        f"Refusal should not create a worktree; got {len(wt_list)} entries:\n" + "\n".join(wt_list)
    )

    # --- Happy path (sanity: guard didn't break the normal flow) ---
    _git(project, "checkout", "master")
    result = proj.run("wt", "create", "task/probe")
    result.expect_ok().expect_stdout_contains("Worktree created", "task/probe")

    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 2, (
        f"Expected 2 worktrees after happy-path create; got {len(wt_list)}:\n" + "\n".join(wt_list)
    )

    # Cleanup the worktree we just created so we don't leak directories
    # outside tmp_path. `wt discard` exits with the worktree handler's
    # standard codes; tolerate either 0 or 2 (partial cleanup is fine in
    # tmp, the dir is under tmp_path and pytest will sweep it).
    proj.run("wt", "discard", "task/probe", "--force")


def test_task_transition_execute_refuses_on_feature_branch(tmp_venv_project) -> None:
    """E2E gate for the second call site (`rack transition ... execute`).

    Covers the path the unit test exercises via `TaskManager.transition()`
    directly — but here through the real binary, real backlog state file,
    and real disk I/O. Without this, a regression that breaks the
    `WorktreeBaseBranchError` propagation through the rack worktree
    extension (e.g. accidentally catching it as a generic `Exception`
    earlier in the chain) would slip past the unit suite.

    1. ``git init -b master`` + commit + .agent layout.
    2. Seed a task and transition it to ``plan``.
    3. ``git checkout -b feat/parking``.
    4. ``rack transition <ID> execute`` → exit 1, stderr/stdout
       names ``Refused`` + the feature branch + ``master``.
    5. Task card on disk is still in ``plan`` state (no partial commit).
    """
    proj = tmp_venv_project
    project = proj.path

    _git_init_on_master(project)

    task_id = _created_id(_ok(_rack(proj, "create", "Probe HATS-518 e2e")))
    _ok(_rack(proj, "transition", task_id, "plan"))
    # The PLAN scaffold is empty; `transition execute` defaults to
    # strict_plan_check=True and would raise EmptyPlanError BEFORE our
    # guard fires. Fill every required section so the per-section gate
    # passes and execution proceeds to the guard (HATS-635).
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    assert plan_path.exists(), f"expected plan scaffold at {plan_path}"
    plan_path.write_text(
        "# Plan\n\n"
        "## Requirements\nprobe\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    # Park HEAD on a feature branch and try to execute. rack renders the
    # generic worktree refusal on stderr and exits 1 (legacy exited 2).
    _git(project, "checkout", "-b", "feat/parking")
    refused = _rack(proj, "transition", task_id, "execute")
    combined = refused.stdout + refused.stderr
    assert refused.returncode == 1, combined
    for marker in ("Refused", "feat/parking", "master"):
        assert marker in combined, f"missing {marker!r}:\n{combined}"

    # Card untouched — still in plan on disk.
    show = _ok(_rack(proj, "context", task_id))
    assert "state: plan" in show.stdout, (
        f"Card should remain in 'plan' state after refused transition; "
        f"`rack context` output tail:\n{show.stdout[-400:]}"
    )

    # No worktree leak — only the main repo's worktree listed.
    wt_list = _git(project, "worktree", "list").stdout.strip().splitlines()
    assert len(wt_list) == 1, (
        f"Refused transition must not create a worktree; got {len(wt_list)}:\n" + "\n".join(wt_list)
    )
