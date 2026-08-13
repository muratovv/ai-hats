"""e2e (HATS-570)

flow:   a developer running temp cleanup script to remove leftover test worktree
        directories
cmds:
    bash scripts/clean-tmp-cruft.sh --force
expect: script removes temporary worktree and pytest directories while preserving caller
        worktree
why:    without tmp cleanup scripts, interrupted test runs leak temporary worktree
        directories in /tmp
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "clean-tmp-cruft.sh"


def _run(sandbox: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the sweeper with TMPDIR pinned to ``sandbox`` (no /tmp bleed).

    The script also scans ``/tmp``; we keep the fixtures under a unique
    sandbox prefix and assert on those specific paths so a busy real
    ``/tmp`` cannot make the test flaky.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env={"TMPDIR": str(sandbox), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A fake temp root pre-seeded with both cruft patterns + a keeper."""
    root = tmp_path / "fake-tmp"
    wt = root / "ai-hats-wt-task-probe-XXXX"
    pyt = root / "pytest-of-probe" / "run0"
    keep = root / "keep-me"
    for d in (wt, pyt, keep):
        d.mkdir(parents=True)
    (wt / "marker").write_text("leak")
    return root, wt, root / "pytest-of-probe", keep


def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file(), f"sweeper script missing: {SCRIPT}"


def test_dry_run_preserves_and_lists(sandbox) -> None:
    root, wt, pyt, keep = sandbox
    cp = _run(root)
    assert cp.returncode == 0, cp.stderr
    # nothing deleted on a dry-run
    assert wt.exists() and pyt.exists() and keep.exists()
    # both cruft dirs are listed; the keeper is not
    assert "ai-hats-wt-task-probe-XXXX" in cp.stdout
    assert "pytest-of-probe" in cp.stdout
    assert "DRY-RUN" in cp.stdout
    assert "keep-me" not in cp.stdout


def test_force_removes_cruft_keeps_others(sandbox) -> None:
    root, wt, pyt, keep = sandbox
    cp = _run(root, "--force")
    assert cp.returncode == 0, cp.stderr
    assert not wt.exists(), "ai-hats-wt-* not removed"
    assert not pyt.exists(), "pytest-of-* not removed"
    assert keep.exists(), "unrelated dir must survive"


def test_force_is_idempotent(sandbox) -> None:
    root, _wt, _pyt, _keep = sandbox
    first = _run(root, "--force")
    assert first.returncode == 0, first.stderr
    second = _run(root, "--force")
    assert second.returncode == 0, second.stderr
    assert "nothing to clean" in second.stdout


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@e.st", "-c", "user.name=t", *args],
        cwd=str(cwd),
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def test_force_spares_a_registered_worktree(tmp_path: Path) -> None:
    """A LIVE worktree git still tracks survives --force; a pruned shell does not.

    The name is all the sweeper had to go on, so ``--force`` would have taken
    every ``ai-hats-wt-*`` — including the 11 worktrees a developer had open
    (HATS-1624). Registration is the proof of reachability that separates them.
    """
    root = tmp_path / "fake-tmp"
    root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "seed")

    live = root / "ai-hats-wt-task-registered-ZZZZ"
    _git(repo, "worktree", "add", "-q", "-b", "task/probe", str(live))

    # Same shape, no admin dir behind it: the leak the sweeper exists to take.
    shell = root / "ai-hats-wt-task-orphan-WWWW"
    shell.mkdir()
    (shell / ".git").write_text("gitdir: /nonexistent/worktrees/gone\n")

    cp = _run(root, "--force")

    assert cp.returncode == 0, cp.stderr
    assert live.exists(), "a registered worktree must survive --force"
    assert not shell.exists(), "an unregistered shell must still be reaped"


def test_never_deletes_cwd_worktree(tmp_path: Path) -> None:
    """A worktree dir the caller is standing in must be skipped."""
    root = tmp_path / "fake-tmp"
    live_wt = root / "ai-hats-wt-task-live-YYYY"
    live_wt.mkdir(parents=True)
    cp = subprocess.run(
        ["bash", str(SCRIPT), "--force"],
        cwd=str(live_wt),  # stand INSIDE the worktree
        env={"TMPDIR": str(root), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stderr
    assert live_wt.exists(), "the in-use worktree must NOT be deleted"
    assert "skip" in cp.stdout
