"""End-to-end coverage for ``ai-hats wt merge`` mid-merge guard
(HATS-587 / F4).

If the main repo already has an unfinished merge in progress (a foreign
``MERGE_HEAD`` — a peer's conflicting merge left mid-resolution, an IDE
"merge branch" the operator never finished, a prior aborted run), the
internal ``git merge --no-ff`` exits 128. Pre-587 that surfaced as a raw
``CalledProcessError`` traceback. The guard now detects the pre-existing
``MERGE_HEAD`` and refuses with an actionable recipe, leaving the worktree
and branch untouched.

Per ``dev_rule_e2e_gate``: change to ``src/ai_hats/cli/worktree.py`` +
``packages/ai-hats-wt/src/ai_hats_wt/manager.py`` requires a real-launcher + real-binary e2e.
CliRunner / pipeline tests do NOT satisfy the gate.

**Fail-under-revert**: remove the ``_refuse_if_mid_merge()`` call from
``_fast_forward_merge`` / ``_squash_merge`` (HATS-602 moved the guard there
from ``WorktreeManager.merge``, inside the base-branch lock) → step (5)
below crashes with a raw exit-128 ``CalledProcessError`` (or completes the
foreign merge), and the no-traceback / refusal assertions fail.

Modelled on ``tests/e2e/test_wt_merge_head_wandered.py``.
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
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_wt_merge_refuses_when_main_repo_mid_merge(shared_launcher, tmp_path):
    """HATS-587 / F4: ``wt merge`` refuses cleanly when the main repo has
    a foreign merge in progress. Real subprocess.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create
         task/midmerge-probe`` from the default branch.
      3. From the worktree, commit on the worktree branch.
      4. In the main repo, start (but do not finish) an unrelated merge:
         ``git merge --no-commit --no-ff foreign`` leaves ``MERGE_HEAD``
         set while HEAD stays on the base branch.
      5. ``ai-hats wt merge task/midmerge-probe`` MUST exit 1 with the
         mid-merge refusal + resolve recipe, and NO Python traceback.
      6. The worktree branch + dir MUST be untouched (guard ran before
         any mutation).
      7. Recovery: ``git merge --abort`` then ``wt merge`` succeeds.
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
    ai_hats("wt", "create", "task/midmerge-probe")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch "):].strip()
            if ref.endswith("/task/midmerge-probe"):
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

    # ---- 4. start a FOREIGN merge in the main repo, leave it unfinished ----
    # A branch off base with one commit, then a `--no-commit --no-ff` merge
    # back onto base: git prepares the merge, stops before committing, and
    # leaves MERGE_HEAD set. HEAD stays on `base_branch`, so the HATS-533
    # HEAD-mismatch guard does NOT pre-empt — the mid-merge guard is what we
    # exercise.
    _git(project, "checkout", "-b", "foreign")
    (project / "foreign.txt").write_text("foreign change\n")
    _git(project, "add", "foreign.txt")
    _git(
        project, "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "foreign commit",
    )
    _git(project, "checkout", base_branch)
    # --no-commit leaves MERGE_HEAD without finishing the merge.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "merge", "--no-commit", "--no-ff", "foreign"],
        cwd=str(project), capture_output=True, text=True, check=False,
    )
    # Sanity: the main repo is genuinely mid-merge.
    assert (project / ".git" / "MERGE_HEAD").exists(), (
        "test setup failed to leave the main repo mid-merge"
    )

    # ---- 5. wt merge refuses cleanly, no traceback ----
    res = ai_hats(
        "wt", "merge", "task/midmerge-probe",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    assert "mid-merge" in combined.lower(), (
        f"mid-merge refusal not surfaced:\n{combined}"
    )
    # No raw Python traceback / CalledProcessError leak (the F4 bug).
    assert "Traceback" not in combined, (
        f"raw traceback leaked — F4 regression:\n{combined}"
    )
    assert "CalledProcessError" not in combined, (
        f"raw CalledProcessError leaked — F4 regression:\n{combined}"
    )
    # The resolve recipe names `git merge --abort` and the retry.
    assert "git merge --abort" in combined, (
        f"resolve recipe `git merge --abort` missing:\n{combined}"
    )
    assert "ai-hats wt merge" in combined, (
        f"retry step `ai-hats wt merge` missing from recipe:\n{combined}"
    )

    # ---- 6. worktree branch + dir untouched (guard pre-mutation) ----
    branches = _git(project, "branch", "--list", "task/midmerge-probe").stdout
    assert "task/midmerge-probe" in branches, (
        f"refusal must preserve the worktree branch:\n{branches}"
    )
    assert wt_path.is_dir(), (
        "refusal must leave the worktree directory intact"
    )

    # ---- 7. recovery: abort the foreign merge, then merge succeeds ----
    _git(project, "merge", "--abort")
    ai_hats("wt", "merge", "task/midmerge-probe", cwd=project)
    branches = _git(project, "branch", "--list", "task/midmerge-probe").stdout
    assert branches.strip() == "", (
        f"worktree branch should be deleted after a successful merge:\n"
        f"{branches!r}"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, (
        f"worktree commit not in history after recovery:\n{log}"
    )
