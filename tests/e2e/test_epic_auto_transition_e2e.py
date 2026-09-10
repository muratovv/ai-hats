"""e2e (HATS-690, HATS-1263)

flow:   a developer completing all child tasks belonging to an epic task card
cmds:
    rack transition HATS-690 done
expect: backlog manager detects all child tasks completed and auto-transitions epic task
        card
why: without epic auto-transition, completed epics remain open requiring manual state
     updates"""

from __future__ import annotations
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.rack]


def _git_init_on_master(project: Path) -> None:
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    (project / ".agent").mkdir(exist_ok=True)
    (project / ".agent" / "STATE.md").write_text("")
    (project / ".agent" / "backlog").mkdir(parents=True, exist_ok=True)
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def _plan_path(project: Path, task_id: str) -> Path:
    return project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"


def _rack(proj, *args: str) -> subprocess.CompletedProcess[str]:
    """The venv's real ``rack`` console script (HATS-1263)."""
    import os

    from ai_hats.paths import ENV_AI_HATS_VENV

    from _helpers.env import clean_env

    env = clean_env(os.environ)
    env.update(proj.env)
    env["AI_HATS_PLAN_ACK"] = "1"
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
    """Parse `Created: <ID> [state] <title>`. Ids are not hardcoded: the default
    prefix differs between the CLIs (legacy TASK, rack HATS)."""
    for line in res.stdout.splitlines():
        if line.strip().startswith("Created:"):
            return line.split()[1]
    raise AssertionError(f"no Created: line in:\n{res.stdout}")


def test_epic_auto_advance_and_reopen_e2e(tmp_venv_project) -> None:
    """Full chain through the real binary: advance to review, then reopen.

    1. ``git init -b master`` + commit + .agent layout.
    2. Create an epic (TASK-001) + 2 children (TASK-002 / TASK-003).
    3. Walk the epic to ``execute`` (fill its plan; real worktree).
    4. Fast-close both children → the second close auto-advances the epic to
       ``review`` and prints the notice.
    5. Reviewer closes the epic to ``done``.
    6. ``task create --parent-task TASK-001`` a 3rd child → the epic auto-reopens
       to ``execute`` and prints the notice.
    """
    proj = tmp_venv_project
    project = proj.path
    _git_init_on_master(project)

    epic = _created_id(_ok(_rack(proj, "create", "Epic")))
    child1 = _created_id(_ok(_rack(proj, "create", "Child 1", "--parent", epic)))
    child2 = _created_id(_ok(_rack(proj, "create", "Child 2", "--parent", epic)))

    # Walk the epic to execute (real worktree). Fill the scaffold so the
    # per-section gate passes (HATS-635).
    _ok(_rack(proj, "transition", epic, "plan"))
    plan_path = _plan_path(project, epic)
    assert plan_path.exists(), f"expected plan scaffold at {plan_path}"
    plan_path.write_text(
        "# Plan\n\n"
        "## Requirements\nepic probe\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    _ok(_rack(proj, "transition", epic, "execute"))

    # Fast-close both children; the SECOND completes the epic → auto-advance.
    _ok(
        _rack(
            proj,
            "transition",
            child1,
            "--state",
            "done",
            "--force",
            "--reason",
            "fast-close",
            "--resolution",
            "shipped",
        )
    )
    closed = _ok(
        _rack(
            proj,
            "transition",
            child2,
            "--state",
            "done",
            "--force",
            "--reason",
            "fast-close",
            "--resolution",
            "shipped",
        )
    )
    # rack echoes the epic work_log delta (cli_kernel.py:_echo_deltas); the
    # legacy `Epic auto-transition:` line has no rack equivalent.
    for marker in (f"epic {epic}:", "advance", "review"):
        assert marker in closed.stdout, f"missing {marker!r}:\n{closed.stdout}"
    show = _ok(_rack(proj, "context", epic))
    assert "state: review" in show.stdout, (
        f"epic should be in review after all children resolved; got:\n{show.stdout[-400:]}"
    )

    # Reviewer closes the epic.
    _ok(_rack(proj, "transition", epic, "done"))

    # New work under the done epic → auto-reopen to execute.
    reopened = _ok(_rack(proj, "create", "Child 3", "--parent", epic))
    for marker in (f"epic {epic}:", "reopen", "execute"):
        assert marker in reopened.stdout, f"missing {marker!r}:\n{reopened.stdout}"
    show = _ok(_rack(proj, "context", epic))
    assert "state: execute" in show.stdout, (
        f"epic should reopen to execute after new child; got:\n{show.stdout[-400:]}"
    )
