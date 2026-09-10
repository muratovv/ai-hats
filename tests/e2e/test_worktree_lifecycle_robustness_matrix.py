"""e2e (HATS-697, HATS-714, HATS-788, HATS-835)

flow:   a developer finalizing an already-merged task whose worktree state file was
        removed
cmds:
    rack transition TST-001 done
expect: transition done short-circuits to done without false state lost errors and
        cleans up branch
why:    already-merged branches with missing state metadata must finalize cleanly

flow:   a developer forcing a task transition to execute with --force
cmds:
    rack transition TST-001 execute --force --reason "shipped on master"
expect: task state moves to execute without spinning up a fresh git worktree
why:    forced execute overrides worktree provisioning when work was shipped out-of-band

flow:   a developer finalizing a task when worktree state metadata contains null
        original_branch
cmds:
    rack transition TST-001 done
expect: transition done is refused with a typed error naming missing original_branch
        field
why:    incomplete worktree state metadata must produce a clean typed error without
        traceback

flow:   a developer finalizing a task from inside its own linked worktree directory
cmds:

    # from inside the linked worktree directory
    rack transition TST-001 done
expect: transition done is refused before teardown and worktree directory is preserved
why:    transition done from inside a worktree must refuse to avoid removing caller cwd"""

from __future__ import annotations
from _helpers.env import consent_grant
from _helpers.git import git as _git

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt


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


def _create_task(rack) -> str:
    res = rack(
        "create",
        "matrix case",
        "--description",
        "epic robustness matrix",
        "--role",
        "assistant",
        "--reviewer",
        "user",
    )
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("Created:"):
            return line.split()[1]
    raise AssertionError(f"could not parse task ID:\n{res.stdout}")


def _fill_plan(project: Path, task_id: str) -> None:
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
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
            current = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current is not None:
            if line[len("branch ") :].strip().endswith(suffix):
                return current
    raise AssertionError(f"worktree for {task_id} not found:\n{listing}")


def _walk_to_execute(rack, project: Path) -> tuple[str, Path]:
    task_id = _create_task(rack)
    rack("transition", task_id, "plan")
    _fill_plan(project, task_id)
    rack("transition", task_id, "execute")
    return task_id, _locate_worktree(project, task_id)


def _state_json(project: Path, task_id: str) -> Path:
    return (
        project / ".agent" / "ai-hats" / "sessions" / "worktrees" / f"task-{task_id.lower()}.json"
    )


# --------------------------------------------------------------------------- #
# Scenarios — each takes (ai_hats, project) and asserts one epic invariant
# --------------------------------------------------------------------------- #


def _scenario_already_merged_state_lost(rack, project: Path) -> None:
    """HATS-697: merged branch + lost state → finalize, no false refusal."""
    task_id, wt = _walk_to_execute(rack, project)
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

    rack("transition", task_id, "document")
    rack("transition", task_id, "review")
    # HATS-1682: `review -> done` is a declared consent point; the answer is
    # scaffolding, and the worktree invariant below is the subject.
    res = rack("transition", task_id, "done", expect_exit=0, extra_env=consent_grant())
    assert "worktree state lost" not in (res.stdout + res.stderr).lower(), (
        f"false state-lost refusal:\n{res.stdout}\n{res.stderr}"
    )
    assert "state: done" in rack("context", task_id).stdout
    assert _git(project, "branch", "--list", task_branch).stdout.strip() == "", (
        "merged branch should be cleaned up by finalize"
    )
    assert _git(project, "rev-parse", base).stdout.strip() == base_sha, (
        "no double-merge: base ref must be unchanged"
    )


def _scenario_forced_execute_no_worktree(rack, project: Path) -> None:
    """HATS-697: a forced execute spins no fresh worktree."""
    task_id = _create_task(rack)
    rack("transition", task_id, "plan")
    _fill_plan(project, task_id)
    res = rack(
        "transition",
        task_id,
        "execute",
        "--force",
        "--reason",
        "shipped on master, correcting state",
        expect_exit=0,
    )
    assert "no worktree created (manual override)" in res.stdout, res.stdout
    assert "state: execute" in rack("context", task_id).stdout
    branches = [
        line[len("branch ") :].strip()
        for line in _git(project, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("branch ")
    ]
    task_branch = f"task/{task_id.lower()}"
    assert not any(b.endswith(f"/{task_branch}") for b in branches), (
        f"forced execute spun a worktree: {branches}"
    )
    assert _git(project, "branch", "--list", task_branch).stdout.strip() == ""


def _scenario_null_original_branch_typed(rack, project: Path) -> None:
    """HATS-714: original_branch=null → typed refusal, not a traceback."""
    task_id, _wt = _walk_to_execute(rack, project)
    state_path = _state_json(project, task_id)
    assert state_path.is_file(), f"state JSON missing: {state_path}"
    data = json.loads(state_path.read_text())
    assert data.get("original_branch"), "precondition: real original_branch"
    data["original_branch"] = None
    state_path.write_text(json.dumps(data, indent=2))

    rack("transition", task_id, "document")
    rack("transition", task_id, "review")
    # HATS-1682: `review -> done` is a declared consent point; the answer is
    # scaffolding, and the worktree invariant below is the subject.
    res = rack("transition", task_id, "done", expect_exit=1, extra_env=consent_grant())
    combined = res.stdout + res.stderr
    # rack keeps this on the generic wt-refusal shape (HATS-1263 ruling Q2):
    # no bespoke recipe, but the refusal must stay typed and name the field.
    assert "refused (worktree)" in combined.lower(), combined
    assert "original_branch" in combined, combined
    assert "Traceback" not in combined, f"leaked traceback:\n{combined}"


def _scenario_in_worktree_done_refused(rack, project: Path) -> None:
    """HATS-788: transition done from inside the linked worktree is refused."""
    task_id, wt = _walk_to_execute(rack, project)
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")
    # Run from INSIDE the worktree → must refuse before any teardown.
    # HATS-1682: `review -> done` is a declared consent point; the answer is
    # scaffolding, and the worktree invariant below is the subject.
    res = rack("transition", task_id, "done", cwd=wt, expect_exit=None, extra_env=consent_grant())
    combined = res.stdout + res.stderr
    assert res.returncode != 0, f"in-worktree close should refuse:\n{combined}"
    assert "linked worktree" in combined.lower(), combined
    assert wt.is_dir(), "refused close must not remove the worktree"
    assert "state: review" in rack("context", task_id).stdout


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

    _bootstrap(ai_hats, project)  # `self init` stays on the ai-hats binary
    SCENARIOS[scenario_id](rack, project)
