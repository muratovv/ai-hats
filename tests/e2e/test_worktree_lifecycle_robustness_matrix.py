"""Epic acceptance matrix for HATS-835 (worktree lifecycle & merge robustness).

Each child of the epic hardened one failure mode of the
``task transition`` / worktree lifecycle and shipped its own focused e2e.
This module is the **capstone**: one parametrized real-launcher matrix that
walks the shared bootstrap once per case and asserts the epic's user-visible
guarantees hold together. It deliberately overlaps the per-child e2e — the
value is a single "the lifecycle is robust as a whole" acceptance gate that
fails loudly if any one invariant regresses.

Covered invariants (child → scenario):
- HATS-697 — an already-merged branch whose worktree state was lost finalizes
  ``done`` without a re-merge instead of a false ``worktree state lost``.
- HATS-697 — a forced ``execute`` spins NO fresh worktree.
- HATS-714 — a state file with ``original_branch: null`` yields a typed
  "incomplete worktree state" refusal, never an opaque traceback.
- HATS-788 — ``transition done`` from INSIDE the task's own linked worktree is
  refused before any teardown.

Per ``dev_rule_e2e_gate``: this exercises the real launcher + real binary.
"""

from __future__ import annotations

import json
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


def _git_clean(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """git with hooks/gpg disabled — for commits/merges in throwaway repos."""
    return _git(cwd, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _bootstrap(ai_hats, project: Path) -> None:
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")


def _create_task(ai_hats) -> str:
    res = ai_hats(
        "task", "create", "matrix case",
        "--description", "epic robustness matrix",
        "--role", "assistant", "--reviewer", "user",
    )
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Created:"):
            return line.split()[1]
    raise AssertionError(f"could not parse task ID:\n{res.stdout}")


def _fill_plan(project: Path, task_id: str) -> None:
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / task_id / "plan.md"
    )
    assert plan_path.is_file(), f"plan scaffold missing: {plan_path}"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nmatrix.\n\n## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do\n\n## Verification Protocol\npytest\n"
    )


def _locate_worktree(project: Path, task_id: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    suffix = f"/task/{task_id.lower()}"
    current: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current is not None:
            if line[len("branch "):].strip().endswith(suffix):
                return current
    raise AssertionError(f"worktree for {task_id} not found:\n{listing}")


def _walk_to_execute(ai_hats, project: Path) -> tuple[str, Path]:
    task_id = _create_task(ai_hats)
    ai_hats("task", "transition", task_id, "plan")
    _fill_plan(project, task_id)
    ai_hats("task", "transition", task_id, "execute")
    return task_id, _locate_worktree(project, task_id)


def _state_json(project: Path, task_id: str) -> Path:
    return (
        project / ".agent" / "ai-hats" / "sessions" / "worktrees"
        / f"task-{task_id.lower()}.json"
    )


# --------------------------------------------------------------------------- #
# Scenarios — each takes (ai_hats, project) and asserts one epic invariant
# --------------------------------------------------------------------------- #

def _scenario_already_merged_state_lost(ai_hats, project: Path) -> None:
    """HATS-697: merged branch + lost state → finalize, no false refusal."""
    task_id, wt = _walk_to_execute(ai_hats, project)
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    task_branch = f"task/{task_id.lower()}"

    _git(wt, "config", "user.email", "e2e@test")
    _git(wt, "config", "user.name", "E2E")
    (wt / "work.txt").write_text("work\n")
    _git(wt, "add", "work.txt")
    _git_clean(wt, "commit", "-m", "work")
    _git_clean(project, "merge", "--no-ff", "--no-edit", task_branch)
    base_sha = _git(project, "rev-parse", base).stdout.strip()

    # Lose the worktree: remove the dir AND its state JSON → load_for_task None.
    _git(project, "worktree", "remove", "--force", str(wt))
    removed = False
    for j in _state_json(project, task_id).parent.glob(f"task-{task_id.lower()}*.json"):
        j.unlink()
        removed = True
    assert removed, "precondition: a worktree state JSON should have existed"

    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")
    res = ai_hats("task", "transition", task_id, "done", expect_exit=0)
    assert "worktree state lost" not in (res.stdout + res.stderr).lower(), (
        f"false state-lost refusal:\n{res.stdout}\n{res.stderr}"
    )
    assert "state: done" in ai_hats("task", "show", task_id).stdout
    assert _git(project, "branch", "--list", task_branch).stdout.strip() == "", (
        "merged branch should be cleaned up by finalize"
    )
    assert _git(project, "rev-parse", base).stdout.strip() == base_sha, (
        "no double-merge: base ref must be unchanged"
    )


def _scenario_forced_execute_no_worktree(ai_hats, project: Path) -> None:
    """HATS-697: a forced execute spins no fresh worktree."""
    task_id = _create_task(ai_hats)
    ai_hats("task", "transition", task_id, "plan")
    _fill_plan(project, task_id)
    res = ai_hats(
        "task", "transition", task_id, "execute",
        "--force", "--reason", "shipped on master, correcting state",
        expect_exit=0,
    )
    assert "No worktree created (forced)" in res.stdout, res.stdout
    assert "state: execute" in ai_hats("task", "show", task_id).stdout
    branches = [
        line[len("branch "):].strip()
        for line in _git(project, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("branch ")
    ]
    task_branch = f"task/{task_id.lower()}"
    assert not any(b.endswith(f"/{task_branch}") for b in branches), (
        f"forced execute spun a worktree: {branches}"
    )
    assert _git(project, "branch", "--list", task_branch).stdout.strip() == ""


def _scenario_null_original_branch_typed(ai_hats, project: Path) -> None:
    """HATS-714: original_branch=null → typed refusal, not a traceback."""
    task_id, _wt = _walk_to_execute(ai_hats, project)
    state_path = _state_json(project, task_id)
    assert state_path.is_file(), f"state JSON missing: {state_path}"
    data = json.loads(state_path.read_text())
    assert data.get("original_branch"), "precondition: real original_branch"
    data["original_branch"] = None
    state_path.write_text(json.dumps(data, indent=2))

    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")
    res = ai_hats("task", "transition", task_id, "done", expect_exit=1)
    combined = res.stdout + res.stderr
    assert "incomplete worktree state" in combined.lower(), combined
    assert "original_branch" in combined, combined
    assert "Traceback" not in combined, f"leaked traceback:\n{combined}"


def _scenario_in_worktree_done_refused(ai_hats, project: Path) -> None:
    """HATS-788: transition done from inside the linked worktree is refused."""
    task_id, wt = _walk_to_execute(ai_hats, project)
    ai_hats("task", "transition", task_id, "document")
    ai_hats("task", "transition", task_id, "review")
    # Run from INSIDE the worktree → must refuse before any teardown.
    res = ai_hats("task", "transition", task_id, "done", cwd=wt, expect_exit=None)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, f"in-worktree close should refuse:\n{combined}"
    assert "linked worktree" in combined.lower(), combined
    assert wt.is_dir(), "refused close must not remove the worktree"
    assert "state: review" in ai_hats("task", "show", task_id).stdout


SCENARIOS = {
    "hats697_already_merged_state_lost": _scenario_already_merged_state_lost,
    "hats697_forced_execute_no_worktree": _scenario_forced_execute_no_worktree,
    "hats714_null_original_branch_typed": _scenario_null_original_branch_typed,
    "hats788_in_worktree_done_refused": _scenario_in_worktree_done_refused,
}


@pytest.mark.integration
@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_worktree_lifecycle_robustness(shared_launcher, tmp_path, scenario_id):
    """Capstone matrix: every epic invariant must hold on the real binary."""
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    _bootstrap(ai_hats, project)
    SCENARIOS[scenario_id](ai_hats, project)
