"""e2e (HATS-1651)

flow:   a developer merging a worktree branch whose content conflicts with the
        base, then running the very same command again
cmds:
    ai-hats wt merge task/conflict-probe
expect: both runs refuse identically, naming the conflicted path, and the main
        checkout is left exactly as it was found — no MERGE_HEAD, no markers
why:    the conflict crashed with a traceback, leaving the main checkout
        mid-merge while reporting the branch "left intact"
"""  # comment-length: allow — the catalog's flow block, one line per field

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.git import init_repo, git as _git

pytestmark = pytest.mark.wt

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd, *, cwd, env, timeout=180, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _mid_merge(repo: Path) -> bool:
    """Whether git considers this checkout to be resolving a merge."""
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _state(repo: Path) -> tuple[str, str]:
    """The two facts a merge must not change when it refuses: where the branch
    points, and what the working tree holds."""
    return (
        _git(repo, "rev-parse", "HEAD").stdout.strip(),
        _git(repo, "status", "--porcelain").stdout,
    )


def _locate(project: Path, branch: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    current: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current is not None:
            if line[len("branch ") :].strip().endswith(f"/{branch}"):
                return current
    raise AssertionError(f"could not locate worktree for {branch}:\n{listing}")


@pytest.mark.integration
def test_e2e_wt_merge_conflict_leaves_no_partial_state(shared_launcher, tmp_path):
    """A conflicting merge must be all-or-nothing, and say which it was.

    Distinct from test_wt_merge_failed_preserves_worktree.py (HATS-587): that
    one collides on an UNTRACKED file, so git refuses before touching anything
    and no partial state can exist. Here the conflict is in tracked content, so
    git starts the merge and stops halfway — the only shape that can leave the
    main checkout mid-merge.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()
    branch = "task/conflict-probe"

    def ai_hats(*args, expect_exit=0, cwd=project):
        return _run([str(launcher_dest), *args], cwd=cwd, env=env, expect_exit=expect_exit)

    init_repo(project, branch="main")
    (project / "CONFLICT.txt").write_text("v1\n")
    _git(project, "add", "CONFLICT.txt")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")
    ai_hats("wt", "create", branch)
    wt_path = _locate(project, branch)

    # Both sides rewrite the same line — `git merge --no-ff` must stop halfway.
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "CONFLICT.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "CONFLICT.txt")
    _git(wt_path, "-c", "core.hooksPath=/dev/null", "commit", "-m", "worktree rewrites the line")

    (project / "CONFLICT.txt").write_text("from-main\n")
    _git(project, "add", "CONFLICT.txt")
    _git(project, "-c", "core.hooksPath=/dev/null", "commit", "-m", "main rewrites the line")

    before = _state(project)

    # Pinned so the flag below is never "simplified" away: conflicting content
    # means the base moved, which IS drift, so a conflict test without
    # --accept-drift passes on the drift refusal without reaching `git merge`.
    drifted = ai_hats("wt", "merge", branch, expect_exit=None)
    assert drifted.returncode != 0, "the base moved, so this must not merge silently"
    assert "drift" in (drifted.stdout + drifted.stderr).lower(), (
        f"expected the drift guard to answer before the merge:\n{drifted.stdout}{drifted.stderr}"
    )

    first = ai_hats("wt", "merge", branch, "--accept-drift", expect_exit=None)
    said = first.stdout + first.stderr

    assert first.returncode != 0, f"a conflicting merge reported success:\n{said}"
    assert "Traceback" not in said, (
        f"the conflict surfaced as an unhandled exception instead of a refusal:\n{said}"
    )
    assert "CONFLICT.txt" in said, f"the refusal does not name the conflicted path:\n{said}"

    assert not _mid_merge(project), (
        "the main checkout was left mid-merge (MERGE_HEAD present) — the next "
        f"worktree operation in this repo is now blocked by it:\n{said}"
    )
    assert _state(project) == before, (
        f"the main checkout changed while the merge refused.\n"
        f"before: {before}\nafter:  {_state(project)}\nsaid:\n{said}"
    )

    # The HATS-587 contract still holds: nothing was torn down.
    assert wt_path.is_dir(), f"a refused merge removed the worktree directory:\n{said}"
    assert branch in _git(project, "branch", "--list", branch).stdout, (
        f"a refused merge deleted the branch:\n{said}"
    )

    # Idempotency: the same command, the same answer. Before HATS-1651 the second
    # run refused with "main repo mid-merge" — cleanup for an operation the first
    # run said it never performed.
    second = ai_hats("wt", "merge", branch, "--accept-drift", expect_exit=None)
    again = second.stdout + second.stderr

    assert second.returncode == first.returncode, (
        f"the retry exited {second.returncode}, the first run {first.returncode}\n{again}"
    )
    assert "mid-merge" not in again, (
        f"the retry blames a mid-merge state the first run left behind:\n{again}"
    )
    assert "CONFLICT.txt" in again, f"the retry does not name the conflicted path:\n{again}"
    assert _state(project) == before, (
        f"the retry changed the main checkout.\nbefore: {before}\nafter: {_state(project)}"
    )
