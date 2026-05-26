"""End-to-end coverage for ``ai-hats wt merge`` HEAD-wandered guard
(HATS-533).

The merge-time twin of HATS-518: if main-repo HEAD has moved off
``_original_branch`` between ``wt create`` and ``wt merge`` (manual
``git checkout``, a peer agent operating directly in the main repo
without a linked worktree, an IDE branch-switch), ``git merge`` in the
main-repo cwd would silently land on the current branch. Same
silent-wrong-branch-merge class as HATS-486 — discovered live in the
HATS-509 session.

Per ``dev_rule_e2e_gate``: change to ``src/ai_hats/cli/worktree.py``
requires a real-launcher + real-binary e2e test. CliRunner / pipeline
tests do NOT satisfy the gate.

**Fail-under-revert**: remove the new ``WorktreeBaseBranchMismatchError``
guard block in ``WorktreeManager.merge`` → step (5) below proceeds with
exit 0, the wandered-feature branch absorbs the worktree commit, and
the negative assertions fail. The test exercises the new behaviour,
not some pre-existing guard.

Modelled on ``tests/e2e/test_wt_merge_drift.py``.
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
def test_e2e_wt_merge_head_wandered_guard(tmp_path):
    """HATS-533: ``wt merge`` refuses when main-repo HEAD is no longer
    on ``_original_branch``. Real subprocess.

    Scenario:
      1. Bootstrap launcher + ``self update`` + ``self init``.
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

    base_branch = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/wandered-probe")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith("/task/wandered-probe"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path:\n{listing}"
    )

    # ---- 3. worktree branch gets its own commit ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "wt-work",
    )

    # ---- 4. simulate HEAD wandering in the main repo ----
    # The classic shape from the HATS-509 incident: between wt create and
    # wt merge, something moves the main-repo HEAD off the merge target.
    _git(project, "checkout", "-b", "wandered-feature")

    # ---- 5. wt merge refuses with mismatch message ----
    res = ai_hats(
        "wt", "merge", "task/wandered-probe",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    assert "base branch mismatch" in combined.lower(), (
        f"mismatch refusal not surfaced:\n{combined}"
    )
    assert "wandered-feature" in combined, (
        f"current branch name missing from refusal:\n{combined}"
    )
    assert base_branch in combined, (
        f"expected branch name (`{base_branch}`) missing from refusal:\n"
        f"{combined}"
    )
    # The recipe must name `git checkout <expected>` so the operator has
    # a copy-pasteable path forward.
    assert f"git checkout {base_branch}" in combined, (
        f"recovery `git checkout {base_branch}` missing from recipe:\n"
        f"{combined}"
    )
    # And the path back to `wt merge` itself.
    assert "ai-hats wt merge" in combined, (
        f"retry step `ai-hats wt merge` missing from recipe:\n{combined}"
    )

    # ---- 6. Critical safety: NO wrong-branch merge happened ----
    # `wandered-feature` MUST NOT carry the worktree commit.
    wandered_log = _git(
        project, "log", "--oneline", "wandered-feature"
    ).stdout
    assert "wt-work" not in wandered_log, (
        f"wandered branch MUST NOT receive the worktree commit "
        f"(this is the bug HATS-533 guards against):\n{wandered_log}"
    )
    # The base branch MUST also be untouched (no merge in either direction).
    base_log = _git(project, "log", "--oneline", base_branch).stdout
    assert "wt-work" not in base_log, (
        f"base branch unexpectedly received the worktree commit:\n"
        f"{base_log}"
    )

    # Worktree branch preserved for retry.
    branches = _git(
        project, "branch", "--list", "task/wandered-probe"
    ).stdout
    assert "task/wandered-probe" in branches, (
        f"refusal must preserve the worktree branch:\n{branches}"
    )

    # ---- 7. recovery path: switch back, merge succeeds ----
    _git(project, "checkout", base_branch)
    merge_res = ai_hats(
        "wt", "merge", "task/wandered-probe",
        cwd=project,
    )
    # Worktree branch is gone after a successful merge.
    branches = _git(
        project, "branch", "--list", "task/wandered-probe"
    ).stdout
    assert branches.strip() == "", (
        f"worktree branch should be deleted after merge:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )
    # And the wt-work commit landed on the base branch.
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, (
        f"worktree commit not in base history after recovery:\n{log}"
    )
