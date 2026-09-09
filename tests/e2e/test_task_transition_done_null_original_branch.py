"""e2e (HATS-714)

flow:   a developer finalizing a task when worktree state metadata contains a null
        original_branch field
cmds:
    # when worktree state metadata contains null original_branch
    rack transition TST-001 done
expect: transition to done is refused with a clean error message identifying the missing
        original_branch value without raising a Python exception
why:    incomplete worktree state metadata must produce a clear actionable error
        instead of an unhandled traceback
"""

from __future__ import annotations
from _helpers.env import consent_grant
from _helpers.git import git as _git

import json
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


@pytest.mark.integration
def test_e2e_task_transition_done_null_original_branch(shared_launcher, tmp_path):
    """HATS-714 on the `task transition done` surface.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. Create a task, walk brainstorm → plan → execute (worktree created,
         `_original_branch=<base>`), then document → review.
      3. Corrupt the worktree state JSON: ``original_branch`` -> ``null``.
      4. ``rack transition <ID> done`` MUST exit 1 with the typed
         "incomplete worktree state" refusal naming ``original_branch`` —
         and stderr MUST carry no Python ``Traceback`` / ``TypeError``.
      5. Card remains in ``review`` (HATS-481 fail-loud: the raise precedes
         ``_save_task``).
    """
    launcher_dest, env, venv = shared_launcher
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
            [str(venv / "bin" / "rack"), *args],
            cwd=cwd,
            env={**env, "AI_HATS_PLAN_ACK": "1", **(extra_env or {})},
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
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

    # ---- 2. create a task and walk it to review ----
    new_res = rack(
        "create",
        "null base test",
        "--description",
        "exercise the HATS-714 incomplete-state refusal",
        "--role",
        "assistant",
        "--reviewer",
        "user",
    )
    task_id = None
    for line in new_res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Created:"):
            task_id = line.split()[1]
            break
    assert task_id and task_id.startswith("TST-"), (
        f"could not parse task ID from:\n{new_res.stdout}"
    )

    rack("transition", task_id, "plan")

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise null original_branch.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    rack("transition", task_id, "execute")
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- 3. corrupt the state file: original_branch -> null ----
    # Done AFTER reaching review so no intermediate transition rewrites it.
    state_path = (
        project / ".agent" / "ai-hats" / "sessions" / "worktrees" / f"task-{task_id.lower()}.json"
    )
    assert state_path.is_file(), f"worktree state file not found at {state_path}"
    data = json.loads(state_path.read_text())
    assert data.get("original_branch"), (
        f"precondition: state should start with a real original_branch, "
        f"got {data.get('original_branch')!r}"
    )
    data["original_branch"] = None
    state_path.write_text(json.dumps(data, indent=2))

    # ---- 4. transition done refuses cleanly, no traceback ----
    # HATS-1682: `review -> done` is a consent point the role declares; the
    # answer is scaffolding, and the worktree layer is what this measures.
    res = rack(
        "transition",
        task_id,
        "done",
        expect_exit=1,
        cwd=project,
        extra_env=consent_grant(),
    )
    combined = res.stdout + res.stderr

    assert "refused (worktree)" in combined.lower(), (
        f"typed refusal not surfaced on the transition-done surface:\n{combined}"
    )
    assert "original_branch" in combined, (
        f"refusal must name the missing `original_branch` field:\n{combined}"
    )
    assert "Traceback" not in res.stderr, (
        f"a Python traceback leaked instead of a typed refusal:\n{res.stderr}"
    )
    assert "TypeError" not in combined, f"the opaque TypeError must be gone:\n{combined}"

    # ---- 5. card remains in `review` (HATS-481 fail-loud) ----
    show = rack("context", task_id)
    assert "state: review" in show.stdout, (
        f"task must remain in `review` after the refusal (the raise "
        f"precedes _save_task):\n{show.stdout}"
    )
