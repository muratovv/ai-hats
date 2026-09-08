"""e2e (HATS-479)

flow:   two developer sessions concurrently creating a worktree for the same branch
cmds:
    ai-hats wt create task/race
expect: exactly one worktree creation succeeds while the loser exits with a friendly
        error
why:    concurrent worktree creation must lock branch allocation to prevent duplicate
        worktrees"""

from __future__ import annotations
from _helpers.git import git as _git

import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats_wt import LifecycleContext, WorktreeManager


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
def test_create_reserves_branch_before_running_hooks(tmp_path: Path):
    _git(tmp_path, "init", "-b", "main")
    _git(
        tmp_path,
        "-c",
        "user.email=e2e@test",
        "-c",
        "user.name=E2E",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    outcomes = []

    class CompetingCreate:
        def on_created(self, ctx: LifecycleContext) -> None:
            outcomes.append(
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; from pathlib import Path; "
                        "from ai_hats_wt import WorktreeManager; "
                        "WorktreeManager(Path(sys.argv[1]), branch_name='task/race', "
                        "state_dir=Path(sys.argv[2])).create()",
                        str(ctx.project_dir),
                        str(ctx.state_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            )

        def before_merge(self, ctx: LifecycleContext) -> None:
            return None

        def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
            return None

    manager = WorktreeManager(
        tmp_path,
        branch_name="task/race",
        state_dir=tmp_path / ".wt",
        lifecycle=CompetingCreate(),
    )

    manager.create()

    assert len(outcomes) == 1
    assert outcomes[0].returncode == 1, outcomes[0].stdout + outcomes[0].stderr
    assert "already exists" in outcomes[0].stderr
    manager.discard()


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
def test_e2e_wt_create_concurrent_same_branch(shared_launcher, tmp_path):
    """Two parallel ``ai-hats wt create task/race`` processes converge on
    exactly one winner with no leaked state."""
    launcher_dest, env, _venv = shared_launcher
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

    # ---- bootstrap project (real git repo + ai-hats init) ----
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
        cmd,
        cwd=str(project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        cmd,
        cwd=str(project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    assert len(losers) == 1, f"expected 1 loser, got exit codes {[o[0] for o in outcomes]}"

    # Loser sees a friendly message — not an opaque traceback.
    loser_stdout_stderr = (losers[0][1] + losers[0][2]).lower()
    assert "already exists" in loser_stdout_stderr, (
        f"loser output missing 'already exists':\nstdout: {losers[0][1]}\nstderr: {losers[0][2]}"
    )
    assert "traceback" not in loser_stdout_stderr, (
        f"loser leaked a traceback:\nstdout: {losers[0][1]}\nstderr: {losers[0][2]}"
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
    assert len(new_dirs) <= 1, f"loser leaked tempdirs (this test): {sorted(new_dirs)}"
