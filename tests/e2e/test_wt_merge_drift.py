"""End-to-end coverage for ``ai-hats wt merge`` drift guard (HATS-457).

Per ``dev_rule_e2e_gate``: changes that touch ``src/ai_hats/cli/worktree.py``
or ``src/ai_hats/worktree.py`` require an e2e test using the real
launcher + real pip install + real ``ai-hats`` binary. The drift guard
implements HYP-017: between ``wt create`` and ``wt merge`` another
agent's worktree may have already advanced the local base branch, and
the second agent's pre-merge ``grep-verify`` becomes silently stale.

**Fail-under-revert**: comment out ``self._check_drift()`` in
``WorktreeManager.merge`` → step (5) below proceeds with exit 0
instead of exit 1, and the test fails. This verifies the test
actually exercises the new behavior (not some pre-existing guard).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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
def test_e2e_wt_merge_drift_guard(tmp_path):
    """HATS-457 drift guard, real subprocess.

    Scenario:
      1. Bootstrap launcher + ``self update`` + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create task/test-drift``.
      3. From the main checkout, add a commit on the default branch
         (simulates "another agent already merged into master").
      4. From the worktree, make a commit on the worktree branch.
      5. ``ai-hats wt merge`` — must exit 1 with a message naming the
         drift file and ``--accept-drift``.
      6. ``ai-hats wt merge --accept-drift`` — must exit 0 and land the
         worktree commit on the default branch.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    # ---- install launcher ----
    _run(
        ["bash", str(INSTALL_LAUNCHER)],
        cwd=tmp_path, env=env, timeout=30,
    )
    assert launcher_dest.is_file()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
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

    ai_hats("self", "update")
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    base_branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/test-drift")

    # Locate the worktree path via `git worktree list` (one path per line,
    # no rich-console width wrapping to fight).
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith("/task/test-drift"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path:\n{listing}"
    )

    # ---- 3. main checkout advances the base branch (the drift) ----
    (project / "drift.txt").write_text("from the other agent\n")
    _git(project, "add", "drift.txt")
    _git(project, "commit", "-m", "main: advance base after worktree create")

    # ---- 4. worktree branch gets its own commit ----
    # Linked worktrees share the parent repo's hooks; disable them for
    # this test commit so privacy / pre-commit installed by `self init`
    # doesn't reject it.
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )

    # ---- 5. wt merge refuses with drift message ----
    # Run from the main project (not the linked worktree) — the launcher
    # resolves the venv from `pwd`, and the worktree directory has no
    # `.agent/` of its own.
    res = ai_hats(
        "wt", "merge", "task/test-drift",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr
    assert "drift" in combined.lower(), (
        f"drift not mentioned in refusal:\n{combined}"
    )
    assert "drift.txt" in combined, (
        f"affected path not listed in refusal:\n{combined}"
    )
    assert "--accept-drift" in combined, (
        f"override flag not advertised:\n{combined}"
    )

    # Worktree branch still exists (refusal preserves it for re-verify).
    branches = _git(project, "branch", "--list", "task/test-drift").stdout
    assert "task/test-drift" in branches, (
        f"drift refusal must preserve the worktree branch:\n{branches}"
    )

    # ---- 6. --accept-drift completes the merge ----
    merge_res = ai_hats(
        "wt", "merge", "task/test-drift", "--accept-drift",
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
