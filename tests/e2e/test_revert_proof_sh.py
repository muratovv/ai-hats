"""e2e (HATS-1899)

flow:   an agent proving a fix is covered — temp-commit, run, reset — instead of
        typing that loop by hand
cmds:
    scripts/revert-proof.sh --from <ref> <path> -- <pytest node id>
expect: green-then-red exits 0 and puts HEAD and the tree back; a dirty tree and
        the main checkout are refused before anything is touched
why:    the loop ends in `git reset --hard`, and the third hand-run of it in one
        session is what moved master by a commit
"""

from __future__ import annotations

import subprocess as sp
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = [pytest.mark.integration, pytest.mark.gates]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "revert-proof.sh"


def _run(cwd: Path, *args: str) -> sp.CompletedProcess:
    return sp.run(  # noqa: S603 - our own script, path from the repo
        [str(SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def sandbox(tmp_path):
    """A repo with a linked worktree, and a `test_probe.py` whose verdict is
    decided by `answer.py` — so reverting the fix really does turn it red."""
    project = tmp_path / "proj"
    project.mkdir()
    init_repo(project)
    (project / "answer.py").write_text("VALUE = 0\n")
    (project / "test_probe.py").write_text(
        "from answer import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n"
    )
    git(project, "add", "-A")
    git(project, "commit", "-m", "before the fix", "--no-verify")
    (project / "answer.py").write_text("VALUE = 1\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "the fix", "--no-verify")

    worktree = tmp_path / "linked"
    git(project, "worktree", "add", "-b", "task/probe", str(worktree))
    return project, worktree


def test_a_covered_fix_reports_green_then_red_and_restores(sandbox):
    """The whole point, and the restore is half of it."""
    _project, worktree = sandbox
    head_before = git(worktree, "rev-parse", "HEAD").stdout.strip()

    result = _run(worktree, "--from", "HEAD~1", "answer.py", "--", "test_probe.py")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "before=green after=red" in result.stderr, result.stderr
    assert git(worktree, "rev-parse", "HEAD").stdout.strip() == head_before, "HEAD moved"
    assert git(worktree, "status", "--porcelain").stdout.strip() == "", "tree left dirty"


def test_an_uncovered_fix_is_reported_not_hidden(sandbox):
    """A test that stays green without the fix is the finding, and it must not
    be reported as success."""
    _project, worktree = sandbox
    (worktree / "test_unrelated.py").write_text("def test_ok():\n    assert True\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "an unrelated test", "--no-verify")
    head_before = git(worktree, "rev-parse", "HEAD").stdout.strip()

    result = _run(worktree, "--from", "HEAD~2", "answer.py", "--", "test_unrelated.py")

    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    assert "NOT COVERED" in result.stderr, result.stderr
    assert git(worktree, "rev-parse", "HEAD").stdout.strip() == head_before, "HEAD moved"


def test_the_main_checkout_is_refused(sandbox):
    """The refusal that exists because the hand-run loop moved master (HATS-1899)."""
    project, _worktree = sandbox
    result = _run(project, "--from", "HEAD~1", "answer.py", "--", "test_probe.py")
    assert result.returncode == 2, result.stderr
    assert "MAIN checkout" in result.stderr, result.stderr


def test_a_dirty_tree_is_refused_before_anything_is_touched(sandbox):
    """`reset --hard` on a dirty tree destroys work with no recovery."""
    _project, worktree = sandbox
    (worktree / "answer.py").write_text("VALUE = 99\n")

    result = _run(worktree, "--from", "HEAD~1", "answer.py", "--", "test_probe.py")

    assert result.returncode == 2, result.stderr
    assert "dirty" in result.stderr, result.stderr
    assert (worktree / "answer.py").read_text() == "VALUE = 99\n", "the edit was destroyed"


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ((), "no path to revert"),
        (("answer.py",), "no pytest node id"),
    ],
)
def test_an_incomplete_invocation_says_which_half_is_missing(sandbox, args, needle):
    _project, worktree = sandbox
    result = _run(worktree, *args)
    assert result.returncode == 2, result.stderr
    assert needle in result.stderr, result.stderr
