"""E2E (ADR-0013 P1 / HATS-849): the lifecycle extension-point wiring fires on
the FSM auto-create / auto-merge path, via the real binary.

The existing `test_wt_hooks_fail_closed.py` drives the `wt` CLI path
(`wt create/merge/discard`). This test guards the OTHER pair of injection sites
introduced by P1 — `state._setup_worktree` (→ `on_created`) and
`state._teardown_worktree` (→ `before_teardown`) — which the CLI-path e2e never
exercises. A real `ai-hats task transition execute` then `... done` walks the
FSM, and we assert both halves of the wiring fired through the real bundle:

- after `execute`: the `wt_in` hook ran (`.seeded`) — the create extension-point
  fired from `_setup_worktree`;
- after `done`: the `wt_out` hook ran on the `merge` event (`.drained`) — the
  teardown extension-point fired from the FSM auto-merge in `_teardown_worktree`.

**fail-under-revert**: drop `lifecycle=HOOK_LIFECYCLE` from the `WorktreeManager`
construction in `state._setup_worktree` (or the `load_for_task` in
`state._teardown_worktree`) and the reconstructed manager runs the no-op bundle —
`.seeded` (or `.drained`) never appears and the matching assertion goes RED.

Per dev_rule_e2e_gate: real bash + real pip + real ai-hats binary,
@pytest.mark.integration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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


def _git(cwd: Path, *args: str):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def _task_state(project: Path, task_id: str) -> str:
    yaml_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
        / task_id / "task.yaml"
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
            cur = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and cur is not None:
            if line.strip().endswith("/" + branch):
                return cur
    return None


@pytest.fixture
def installed_launcher(shared_launcher, tmp_path_factory):
    """Clean env on the session venv (HATS-685/582): pop PYTHONPATH (else the
    launcher imports the source tree without ``library/``) and isolate HOME."""
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wtfsm-home"))
    return launcher, env, shared_venv


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
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "e2e-wthook-role", "--no-wizard", "--task-prefix", "TST"],
        cwd=project, env=env,
    )


@pytest.mark.integration
def test_fsm_transition_fires_wt_in_and_wt_out(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0, timeout=120):
        return _run([str(launcher), *args], cwd=project, env=env,
                    timeout=timeout, expect_exit=expect_exit)

    task_id = "TST-001"
    branch = f"task/{task_id.lower()}"
    ai("task", "create", "FSM lifecycle wiring", "-d", "wt_in/wt_out via FSM",
       "--id", task_id)
    ai("task", "transition", task_id, "plan")

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
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
    ai("task", "transition", task_id, "execute")
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
    _git(wt, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "commit", "-m", "worktree work")

    ai("task", "transition", task_id, "document")
    ai("task", "transition", task_id, "review")

    # ---- done: _teardown_worktree fires before_teardown → wt_out (.drained) ----
    ai("task", "transition", task_id, "done")
    assert _task_state(project, task_id) == "done"
    drained = project / ".drained"
    assert drained.exists() and "merge" in drained.read_text(), (
        "wt_out did not fire on the FSM auto-merge — _teardown_worktree is not "
        "injecting the hook-running bundle (before_teardown ran the no-op)"
    )
    assert _wt_path(project, branch) is None  # merged + torn down
