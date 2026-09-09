"""e2e (HATS-457)

flow:   a developer merging a worktree branch when base branch has advanced since
        creation
cmds:
    # when base branch has new commits since worktree creation
    ai-hats wt merge task/test-drift
expect: merge is refused detailing drift files unless --accept-drift is passed
why:    base branch drift must be flagged to prevent overwriting concurrent changes
"""

from __future__ import annotations
from _helpers.git import git as _git

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


def _git_no_hooks(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Linked worktrees inherit the parent repo's hooks; `self init` installs
    privacy/pre-commit ones that would reject these synthetic commits."""
    return _git(cwd, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args)


def _locate_worktree(project: Path, branch: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch ") :].strip().endswith(f"/{branch}"):
                assert current_path.is_dir(), f"worktree path missing: {current_path}"
                return current_path
    raise AssertionError(f"could not locate worktree for {branch}:\n{listing}")


@pytest.mark.integration
def test_e2e_wt_merge_drift_guard(shared_launcher, tmp_path):
    """HATS-457 drift guard, real subprocess.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create task/test-drift``.
      3. From the main checkout, add a commit on the default branch
         (simulates "another agent already merged into master").
      4. From the worktree, make a commit on the worktree branch.
      5. ``ai-hats wt merge`` — must exit 1 with a message naming the
         drift file and ``--accept-drift``.
      6. ``ai-hats wt merge --accept-drift`` — must exit 0 and land the
         worktree commit on the default branch.
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
    # Initialize a real git repo first — `ai-hats wt create` requires
    # the project to be a git repo with at least one commit.
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

    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/test-drift")

    wt_path = _locate_worktree(project, "task/test-drift")

    # ---- 3. main checkout advances the base branch (the drift) ----
    (project / "drift.txt").write_text("from the other agent\n")
    _git(project, "add", "drift.txt")
    _git(project, "commit", "-m", "main: advance base after worktree create")

    # ---- 4. worktree branch gets its own commit ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git_no_hooks(wt_path, "commit", "-m", "wt-work")

    # ---- 5. wt merge refuses with drift message ----
    # Run from the main project (not the linked worktree) — the launcher
    # resolves the venv from `pwd`, and the worktree directory has no
    # `.agent/` of its own.
    res = ai_hats(
        "wt",
        "merge",
        "task/test-drift",
        expect_exit=1,
        cwd=project,
    )
    combined = res.stdout + res.stderr
    assert "drift" in combined.lower(), f"drift not mentioned in refusal:\n{combined}"
    assert "drift.txt" in combined, f"affected path not listed in refusal:\n{combined}"
    assert "--accept-drift" in combined, f"override flag not advertised:\n{combined}"
    # HATS-1307: the recipe leads with the remedy that clears the guard.
    assert f"git rebase {base_branch}" in combined, (
        f"rebase-first recipe missing from refusal:\n{combined}"
    )

    # Worktree branch still exists (refusal preserves it for re-verify).
    branches = _git(project, "branch", "--list", "task/test-drift").stdout
    assert "task/test-drift" in branches, (
        f"drift refusal must preserve the worktree branch:\n{branches}"
    )

    # ---- 6. --accept-drift completes the merge ----
    merge_res = ai_hats(
        "wt",
        "merge",
        "task/test-drift",
        "--accept-drift",
        cwd=project,
    )

    # Worktree branch is gone after a successful merge.
    branches = _git(project, "branch", "--list", "task/test-drift").stdout
    assert branches.strip() == "", (
        f"worktree branch should be deleted after merge:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )

    # The wt-work commit (empty) landed on the base branch (as a merge parent
    # under --no-ff).
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, (
        f"worktree commit not in base history:\n{log}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )


@pytest.mark.integration
def test_e2e_rebased_branch_merges_without_accept_drift(shared_launcher, tmp_path):
    """HATS-1307: rebasing clears the guard — the flag-free path must work.

    Pre-1307 the guard compared a create-time snapshot, so a branch sitting
    exactly on the moved base was still refused and the only exit was
    ``--accept-drift`` — a flag ``rack transition done`` cannot pass, which
    dead-ended the auto-merge path.

    **Fail-under-revert**: restore the snapshot comparison in
    ``WorktreeManager._check_drift`` → the flag-free merge below exits 1.
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

    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")
    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    ai_hats("wt", "create", "task/test-rebase")
    wt_path = _locate_worktree(project, "task/test-rebase")

    # Base moves under the worktree (another agent merged first).
    (project / "drift.txt").write_text("from the other agent\n")
    _git(project, "add", "drift.txt")
    _git(project, "commit", "-m", "main: advance base after worktree create")

    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git_no_hooks(wt_path, "commit", "-m", "wt-work")

    # The remedy the refusal recommends — after this the branch holds every
    # base commit, so there is nothing stale left to re-verify.
    _git_no_hooks(wt_path, "rebase", base_branch)

    merge_res = ai_hats("wt", "merge", "task/test-rebase", cwd=project)

    branches = _git(project, "branch", "--list", "task/test-rebase").stdout
    assert branches.strip() == "", (
        f"rebased branch should merge flag-free:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )
    log = _git(project, "log", base_branch, "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history:\n{log}"
    assert (project / "drift.txt").exists(), "base commit lost by the merge"
