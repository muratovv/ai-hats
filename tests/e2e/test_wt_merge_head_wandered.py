"""e2e (HATS-486, HATS-509, HATS-518, HATS-533)

flow:   a developer merging a worktree branch when main repository HEAD is on a
        different branch
cmds:
    # when main HEAD is checked out on a different branch than base
    ai-hats wt merge task/wandered-probe
expect: merge is refused with instructions to checkout base branch before retrying
why:    wt merge requires main repository HEAD to match base branch to prevent
        wrong-branch merges
"""

from __future__ import annotations
from _helpers.git import init_repo, git as _git

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
def test_e2e_wt_merge_head_wandered_guard(shared_launcher, tmp_path):
    """HATS-533: ``wt merge`` refuses when main-repo HEAD is no longer
    on ``_original_branch``. Real subprocess.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create
         task/wandered-probe`` from the default branch (master/main).
      3. From the worktree, make a commit on the worktree branch.
      4. In the main repo, ``git checkout -b wandered-feature``
         (simulates HEAD wandering: peer agent / manual checkout / IDE).
      5. ``ai-hats wt merge task/wandered-probe`` MUST exit 1 with the
         mismatch message naming current=`wandered-feature`,
         expected=<base>, and the recipe.
      6. ``wandered-feature`` MUST NOT carry the worktree commit
         (the silent wrong-branch merge we're guarding against).
      7. Recovery: ``git checkout <base>; ai-hats wt merge ...`` succeeds
         and the worktree commit lands on the right branch.
    """
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

    # ---- 1. bootstrap project ----
    init_repo(project, branch="main")
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

    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/wandered-probe")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch ") :].strip()
            if ref.endswith("/task/wandered-probe"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), f"could not locate worktree path:\n{listing}"

    # ---- 3. worktree branch gets its own commit ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "wt-work",
    )

    # ---- 4. simulate HEAD wandering in the main repo ----
    # The classic shape from the HATS-509 incident: between wt create and
    # wt merge, something moves the main-repo HEAD off the merge target.
    _git(project, "checkout", "-b", "wandered-feature")

    # ---- 5. wt merge refuses with mismatch message ----
    res = ai_hats(
        "wt",
        "merge",
        "task/wandered-probe",
        expect_exit=1,
        cwd=project,
    )
    combined = res.stdout + res.stderr

    assert "base branch mismatch" in combined.lower(), f"mismatch refusal not surfaced:\n{combined}"
    assert "wandered-feature" in combined, f"current branch name missing from refusal:\n{combined}"
    assert base_branch in combined, (
        f"expected branch name (`{base_branch}`) missing from refusal:\n{combined}"
    )
    # The recipe must name `git checkout <expected>` so the operator has
    # a copy-pasteable path forward.
    assert f"git checkout {base_branch}" in combined, (
        f"recovery `git checkout {base_branch}` missing from recipe:\n{combined}"
    )
    # And the path back to `wt merge` itself.
    assert "ai-hats wt merge" in combined, (
        f"retry step `ai-hats wt merge` missing from recipe:\n{combined}"
    )

    # ---- 6. Critical safety: NO wrong-branch merge happened ----
    # `wandered-feature` MUST NOT carry the worktree commit.
    wandered_log = _git(project, "log", "--oneline", "wandered-feature").stdout
    assert "wt-work" not in wandered_log, (
        f"wandered branch MUST NOT receive the worktree commit "
        f"(this is the bug HATS-533 guards against):\n{wandered_log}"
    )
    # The base branch MUST also be untouched (no merge in either direction).
    base_log = _git(project, "log", "--oneline", base_branch).stdout
    assert "wt-work" not in base_log, (
        f"base branch unexpectedly received the worktree commit:\n{base_log}"
    )

    # Worktree branch preserved for retry.
    branches = _git(project, "branch", "--list", "task/wandered-probe").stdout
    assert "task/wandered-probe" in branches, (
        f"refusal must preserve the worktree branch:\n{branches}"
    )

    # ---- 7. recovery path: switch back, merge succeeds ----
    _git(project, "checkout", base_branch)
    merge_res = ai_hats(
        "wt",
        "merge",
        "task/wandered-probe",
        cwd=project,
    )
    # Worktree branch is gone after a successful merge.
    branches = _git(project, "branch", "--list", "task/wandered-probe").stdout
    assert branches.strip() == "", (
        f"worktree branch should be deleted after merge:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )
    # And the wt-work commit landed on the base branch.
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history after recovery:\n{log}"
