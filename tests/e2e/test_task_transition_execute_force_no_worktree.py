"""End-to-end coverage for ``ai-hats task transition <ID> execute --force``
NOT creating a fresh worktree (HATS-697 / PROX-287).

A forced ``→ execute`` is a manual state correction (typically for
retrospective shipped-on-master work), not the start of isolated work.
Before HATS-697 it still spun a fresh worktree off HEAD, orphaning the
retro work that already lived in the main tree. The forced path must now
flip state only and leave the worktree decision to the operator.

**Fail-under-revert**: restore the unconditional ``_setup_worktree`` on the
execute branch of ``state.py:transition`` (drop the ``elif force`` skip) →
``git worktree list`` gains a ``task/<id>`` worktree and the
"no extra worktree" assertion below fails.

Per ``dev_rule_e2e_gate``: HATS-697 touches ``src/ai_hats/cli/`` +
``src/ai_hats/state.py``, so a real-launcher + real-binary e2e is mandatory.

Modelled on ``tests/e2e/test_task_transition_done_already_merged_head_wandered.py``.
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
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


def _worktree_branches(project: Path) -> list[str]:
    """Branch refs across all registered git worktrees of ``project``."""
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    branches: list[str] = []
    for line in listing.splitlines():
        if line.startswith("branch "):
            branches.append(line[len("branch "):].strip())
    return branches


@pytest.mark.integration
def test_e2e_transition_execute_force_creates_no_worktree(shared_launcher, tmp_path):
    """A forced execute flips state without spinning a worktree.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit.
      3. Create a task, walk brainstorm → plan, fill the plan.
      4. ``transition <ID> execute --force --reason ...`` MUST exit 0.
      5. State reaches ``execute`` but NO ``task/<id>`` worktree exists —
         only the main worktree is registered.
      6. The output names the deliberate skip.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap ----
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

    # ---- 2. create task → plan (scaffold) → fill the plan ----
    new_res = ai_hats(
        "task", "create", "forced execute no worktree",
        "--description", "exercise the HATS-697 forced-execute no-worktree path",
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
        "# Plan\n\n## Requirements\nforced execute must not spin a worktree.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )

    # Baseline: only the main worktree, no task branch yet.
    task_branch = f"task/{task_id.lower()}"
    before = _worktree_branches(project)
    assert not any(b.endswith(f"/{task_branch}") for b in before), (
        f"unexpected task worktree before forced execute:\n{before}"
    )

    # ---- 3. forced execute MUST NOT create a worktree ----
    res = ai_hats(
        "task", "transition", task_id, "execute",
        "--force", "--reason", "shipped on master, correcting state",
        expect_exit=0,
    )
    assert "No worktree created (forced)" in res.stdout, (
        f"forced execute did not announce the deliberate skip:\n{res.stdout}"
    )

    # ---- 4. state advanced, but NO task worktree was registered ----
    show = ai_hats("task", "show", task_id)
    assert "state: execute" in show.stdout, (
        f"task did not reach `execute`:\n{show.stdout}"
    )
    after = _worktree_branches(project)
    assert not any(b.endswith(f"/{task_branch}") for b in after), (
        f"🐛 forced execute spun a fresh worktree (HATS-697 regression):\n{after}"
    )
    # The task branch itself must not exist either — no worktree, no branch.
    assert _git(project, "branch", "--list", task_branch).stdout.strip() == "", (
        "forced execute created a task branch despite skipping the worktree"
    )
