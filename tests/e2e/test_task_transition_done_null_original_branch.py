"""End-to-end coverage for ``ai-hats task transition <ID> done`` on a
worktree state file whose ``original_branch`` is ``null`` (HATS-714).

Sibling to ``test_wt_merge_null_original_branch.py`` covering the
``task transition done`` surface specifically — ``transition done``
auto-merges via the same ``WorktreeManager.merge`` that ``wt merge`` uses,
so a state file missing ``original_branch`` would re-traceback here too.
Per ``dev_rule_e2e_gate`` each ``cli/`` surface touched needs its own
real-subprocess test.

**Fail-under-revert**: remove either the ``WorktreeStateIncompleteError``
guard at the top of ``WorktreeManager.merge`` OR the ``except
WorktreeStateIncompleteError`` handler in ``cli/task.py task_transition``
→ ``transition done`` reverts to dumping an opaque traceback (TypeError
without the guard, unhandled WorktreeStateIncompleteError without the
handler). The ``"incomplete worktree state"`` / ``"Traceback" not in
stderr`` assertions then fail.

Modelled on ``tests/e2e/test_task_transition_done_head_wandered.py``.
"""

from __future__ import annotations

import json
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
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_task_transition_done_null_original_branch(shared_launcher, tmp_path):
    """HATS-714 on the `task transition done` surface.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. Create a task, walk brainstorm → plan → execute (worktree created,
         `_original_branch=<base>`), then document → review.
      3. Corrupt the worktree state JSON: ``original_branch`` -> ``null``.
      4. ``ai-hats task transition <ID> done`` MUST exit 1 with the typed
         "incomplete worktree state" refusal naming ``original_branch`` —
         and stderr MUST carry no Python ``Traceback`` / ``TypeError``.
      5. Card remains in ``review`` (HATS-481 fail-loud: the raise precedes
         ``_save_task``).
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
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

    # ---- 2. create a task and walk it to review ----
    new_res = ai_hats(
        "task", "create", "null base test",
        "--description", "exercise the HATS-714 incomplete-state refusal",
        "--role", "assistant",
        "--reviewer", "user",
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

    ai_hats("task", "transition", task_id, "plan")

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise null original_branch.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    ai_hats("task", "transition", task_id, "execute")
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")

    # ---- 3. corrupt the state file: original_branch -> null ----
    # Done AFTER reaching review so no intermediate transition rewrites it.
    state_path = (
        project / ".agent" / "ai-hats" / "sessions" / "worktrees"
        / f"task-{task_id.lower()}.json"
    )
    assert state_path.is_file(), (
        f"worktree state file not found at {state_path}"
    )
    data = json.loads(state_path.read_text())
    assert data.get("original_branch"), (
        f"precondition: state should start with a real original_branch, "
        f"got {data.get('original_branch')!r}"
    )
    data["original_branch"] = None
    state_path.write_text(json.dumps(data, indent=2))

    # ---- 4. transition done refuses cleanly, no traceback ----
    res = ai_hats(
        "task", "transition", task_id, "done",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    assert "incomplete worktree state" in combined.lower(), (
        f"typed refusal not surfaced on the transition-done surface:\n"
        f"{combined}"
    )
    assert "original_branch" in combined, (
        f"refusal must name the missing `original_branch` field:\n{combined}"
    )
    assert "Traceback" not in res.stderr, (
        f"a Python traceback leaked instead of a typed refusal:\n"
        f"{res.stderr}"
    )
    assert "TypeError" not in combined, (
        f"the opaque TypeError must be gone:\n{combined}"
    )

    # ---- 5. card remains in `review` (HATS-481 fail-loud) ----
    show = ai_hats("task", "show", task_id)
    assert "state: review" in show.stdout, (
        f"task must remain in `review` after the refusal (the raise "
        f"precedes _save_task):\n{show.stdout}"
    )
