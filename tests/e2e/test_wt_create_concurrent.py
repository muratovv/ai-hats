"""End-to-end coverage for HATS-479 — concurrent ``ai-hats wt create``.

Per ``dev_rule_e2e_gate``: changes to ``src/ai_hats/cli/worktree.py`` or
``src/ai_hats/wt/manager.py`` require an e2e test using the real launcher
+ real ``ai-hats`` binary. This test exercises the L1+L2+L4 defense:

* two concurrent ``ai-hats wt create task/<same>`` processes,
* exactly one exit 0,
* loser exits 1 with a human-readable "already exists" message
  (NOT an opaque ``CalledProcessError`` traceback),
* exactly one branch on disk,
* exactly one state JSON under ``.agent/ai-hats/sessions/worktrees/``,
* no leaked ``/tmp/ai-hats-wt-task-<same>-*`` directories beyond the
  winner's worktree.

**Fail-under-revert** (mandatory per e2e gate):
comment out ``with _acquire_create_lock(...):`` in
``WorktreeManager.create()`` → both processes race past L2, both call
``git worktree add``, both can exit 0 (state.json race-overwritten) or
the loser exits 1 with an opaque ``CalledProcessError`` lacking the
"already exists" friendly message. Either way, an assertion fails.
"""

from __future__ import annotations

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
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


@pytest.mark.integration
def test_e2e_wt_create_concurrent_same_branch(shared_launcher, tmp_path):
    """Two parallel ``ai-hats wt create task/race`` processes converge on
    exactly one winner with no leaked state."""
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project (real git repo + ai-hats init) ----
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

    # ---- race two `wt create task/race` ----
    branch = "task/race"
    # Snapshot existing /tmp dirs BEFORE the race so the leak assertion
    # below ignores worktree dirs left over from prior test runs (mkdtemp
    # writes to the system temp root, outside tmp_path's cleanup scope).
    import tempfile
    tmp_root = Path(tempfile.gettempdir())
    prefix = "ai-hats-wt-task-race-"
    pre_existing = {p.name for p in tmp_root.iterdir() if p.name.startswith(prefix)}

    cmd = [str(launcher_dest), "wt", "create", branch]
    p1 = subprocess.Popen(
        cmd, cwd=str(project), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    p2 = subprocess.Popen(
        cmd, cwd=str(project), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out1, err1 = p1.communicate(timeout=60)
    out2, err2 = p2.communicate(timeout=60)

    outcomes = [(p1.returncode, out1, err1), (p2.returncode, out2, err2)]
    winners = [o for o in outcomes if o[0] == 0]
    losers = [o for o in outcomes if o[0] != 0]
    assert len(winners) == 1, (
        f"expected 1 winner, got exit codes {[o[0] for o in outcomes]}\n"
        f"p1 stdout: {out1}\np1 stderr: {err1}\n"
        f"p2 stdout: {out2}\np2 stderr: {err2}"
    )
    assert len(losers) == 1, (
        f"expected 1 loser, got exit codes {[o[0] for o in outcomes]}"
    )

    # Loser sees a friendly message — not an opaque traceback.
    loser_stdout_stderr = (losers[0][1] + losers[0][2]).lower()
    assert "already exists" in loser_stdout_stderr, (
        f"loser output missing 'already exists':\n"
        f"stdout: {losers[0][1]}\nstderr: {losers[0][2]}"
    )
    assert "traceback" not in loser_stdout_stderr, (
        f"loser leaked a traceback:\n"
        f"stdout: {losers[0][1]}\nstderr: {losers[0][2]}"
    )

    # ---- post-conditions ----
    # Exactly one branch.
    branch_list = _git(project, "branch", "--list", branch).stdout
    assert branch_list.count(branch) == 1, branch_list

    # Exactly one state JSON.
    state_dir = project / ".agent" / "ai-hats" / "sessions" / "worktrees"
    state_files = list(state_dir.glob("task-race.json"))
    assert len(state_files) == 1, f"state files: {state_files}"

    # No leaked tempdir from the loser. Only dirs CREATED during this test
    # are considered (delta against pre_existing). At most one new dir should
    # exist — the winner's worktree. Two means the loser leaked.
    after = {p.name for p in tmp_root.iterdir() if p.name.startswith(prefix)}
    new_dirs = after - pre_existing
    assert len(new_dirs) <= 1, (
        f"loser leaked tempdirs (this test): {sorted(new_dirs)}"
    )
