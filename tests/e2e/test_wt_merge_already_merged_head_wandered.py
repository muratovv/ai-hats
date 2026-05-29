"""End-to-end coverage for ``ai-hats wt merge`` when the worktree branch is
ALREADY merged into its base and the main checkout HEAD has wandered to a
foreign branch (HATS-596).

Merge-surface twin of ``test_task_transition_done_already_merged_head_wandered``
and the complement of ``test_wt_merge_head_wandered.py`` (HATS-533): there the
work is NOT merged and a wandered HEAD MUST refuse (wrong-branch-merge risk);
here the work IS merged into base, so no ``git merge`` is needed and the HEAD
position is irrelevant — ``wt merge`` MUST succeed via the checkout-independent
already-merged short-circuit.

Per ``dev_rule_e2e_gate``: behaviour reachable through
``src/ai_hats/cli/worktree.py`` needs a real-launcher + real-binary e2e test.

**Fail-under-revert**: remove the HATS-596 short-circuit in
``WorktreeManager.merge`` → the HATS-533 HEAD-mismatch guard fires → ``wt
merge`` exits 1 with "base branch mismatch", and the exit-0 / branch-deleted
assertions below fail.

Modelled on ``tests/e2e/test_wt_merge_head_wandered.py``.
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
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_wt_merge_already_merged_head_wandered(shared_launcher, tmp_path):
    """HATS-596: ``wt merge`` succeeds when the branch is already an
    ancestor of base, regardless of where main-repo HEAD points.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create
         task/already-probe`` from the default branch.
      3. From the worktree, commit on the worktree branch.
      4. Merge the branch into base in the main repo (``--no-ff``) — the
         work is now fully integrated.
      5. In the main repo, ``git checkout -b wandered-feature`` + leave
         uncommitted WIP.
      6. ``ai-hats wt merge task/already-probe`` MUST exit 0 (short-circuit,
         no re-merge, no mismatch refusal).
      7. Worktree dir + branch torn down; main checkout untouched; base ref
         unchanged (no double-merge).
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
    base_branch = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert base_branch, "no checked-out branch after bootstrap"

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/already-probe")
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith("/task/already-probe"):
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

    # ---- 4. merge the branch into base in the main repo ----
    _git(
        project, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "merge", "--no-ff", "--no-edit", "task/already-probe",
    )
    base_sha_after_merge = _git(
        project, "rev-parse", base_branch
    ).stdout.strip()

    # ---- 5. simulate HEAD wandering + uncommitted WIP in the main repo ----
    _git(project, "checkout", "-b", "wandered-feature")
    (project / "foreign-wip.txt").write_text("concurrent WIP\n")

    # ---- 6. wt merge MUST SUCCEED (short-circuit) ----
    res = ai_hats("wt", "merge", "task/already-probe", expect_exit=0)
    combined = res.stdout + res.stderr
    assert "base branch mismatch" not in combined.lower(), (
        f"false mismatch refusal — HATS-596 short-circuit not applied:\n"
        f"{combined}"
    )

    # ---- 7. worktree + branch torn down ----
    assert not wt_path.exists(), f"worktree dir not removed: {wt_path}"
    branches = _git(
        project, "branch", "--list", "task/already-probe"
    ).stdout.strip()
    assert branches == "", f"task branch not deleted: {branches!r}"

    # main checkout untouched: still wandered, WIP intact
    head_now = _git(
        project, "rev-parse", "--abbrev-ref", "HEAD"
    ).stdout.strip()
    assert head_now == "wandered-feature", (
        f"main checkout HEAD moved (should be untouched): {head_now}"
    )
    assert (project / "foreign-wip.txt").read_text() == "concurrent WIP\n", (
        "foreign uncommitted WIP was clobbered"
    )

    # no double-merge: base ref unchanged
    assert _git(
        project, "rev-parse", base_branch
    ).stdout.strip() == base_sha_after_merge, (
        "base branch was re-merged — short-circuit should NOT run git merge"
    )
