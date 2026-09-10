"""e2e (HATS-849)

flow:   a developer walking a task through lifecycle states with registered hooks
cmds:
    rack transition TST-001 execute
    rack transition TST-001 done
expect: wt_in hook fires on transition to execute and wt_out hook fires on transition
        to done
why:    task state machine transitions must trigger worktree setup and teardown
        callbacks
"""

from __future__ import annotations
from _helpers.git import git as _git

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.rack, pytest.mark.wt]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "wt_hook_lib"


def _run(cmd, *, cwd, env, timeout=180, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
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
    for line in yaml_path.read_text().splitlines():
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no state field in {yaml_path}")


def _wt_path(project: Path, branch: str) -> Path | None:
    out = _git(project, "worktree", "list", "--porcelain").stdout
    cur: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and cur is not None:
            if line.strip().endswith("/" + branch):
                return cur
    return None


def _init(launcher: Path, env: dict, project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _git(project, "add", "libraries")
    _git(project, "commit", "-m", "lib")
    _run(
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "e2e-wthook-role",
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=project,
        env=env,
    )


@pytest.mark.integration
def test_fsm_transition_fires_wt_in_and_wt_out(installed_launcher, tmp_path):
    launcher, base_env, venv = installed_launcher
    rack_bin = venv / "bin" / "rack"
    # plan → execute is consent-gated; the gate is not this test's subject.
    env = {**base_env, "AI_HATS_PLAN_ACK": "1"}
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def rack(*args, expect_exit=0, timeout=120):
        return _run(
            [str(rack_bin), *args], cwd=project, env=env, timeout=timeout, expect_exit=expect_exit
        )

    task_id = "TST-001"
    branch = f"task/{task_id.lower()}"
    rack("create", "FSM lifecycle wiring", "--description", "wt_in/wt_out via FSM", "--id", task_id)
    rack("transition", task_id, "plan")

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        f"# {task_id} plan\n\n"
        "## Requirements\nFire wt_in on execute, wt_out on done.\n\n"
        "## Approach & counter\nWalk the FSM and assert the hooks fired.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] walk\n\n"
        "## Verification Protocol\nassert .seeded + .drained\n"
    )

    # ---- execute: _setup_worktree fires on_created → wt_in (.seeded) ----
    rack("transition", task_id, "execute")
    assert (project / ".seeded").exists(), (
        "wt_in did not fire on the FSM execute path — _setup_worktree is not "
        "injecting the hook-running bundle (on_created ran the no-op)"
    )

    # ---- commit real work in the worktree so `done` does a true merge ----
    wt = _wt_path(project, branch)
    assert wt is not None and wt.is_dir(), f"worktree for {branch} not found"
    _git(wt, "config", "user.email", "e2e@test")
    _git(wt, "config", "user.name", "E2E")
    (wt / "work.txt").write_text("payload\n")
    _git(wt, "add", "work.txt")
    _git(
        wt,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "worktree work",
    )

    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- done: _teardown_worktree fires before_teardown → wt_out (.drained) ----
    rack("transition", task_id, "done")
    assert _task_state(project, task_id) == "done"
    drained = project / ".drained"
    assert drained.exists() and "merge" in drained.read_text(), (
        "wt_out did not fire on the FSM auto-merge — _teardown_worktree is not "
        "injecting the hook-running bundle (before_teardown ran the no-op)"
    )
    assert _wt_path(project, branch) is None  # merged + torn down
