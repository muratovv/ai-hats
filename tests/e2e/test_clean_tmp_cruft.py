"""e2e (HATS-570, HATS-1624)

flow:   a developer (and the pre-push gate) sweeping leftover test cruft out of
        the temp root while other runs and agent sessions are working
cmds:
    bash scripts/clean-tmp-cruft.sh [--dry-run|--force]
expect: reaps only what it can prove dead — an unregistered worktree shell, a
        pytest run dir whose .lock names an exited pid — and keeps live
        worktrees, live runs, and anything it cannot judge
why:    the sweeper ran on every gate and deleted nothing (dry-run only),
        while 145 GB of killed-run residue accumulated in TMPDIR
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "clean-tmp-cruft.sh"
_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def _run(sandbox: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the sweeper with TMPDIR pinned to ``sandbox`` (no /tmp bleed).

    The script also scans ``/tmp``; we keep the fixtures under a unique
    sandbox prefix and assert on those specific paths so a busy real
    ``/tmp`` cannot make the test flaky.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env={"TMPDIR": str(sandbox), **_ENV},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_dir(root: Path, name: str, *, pid: int | None) -> Path:
    """A pytest run dir, optionally locked by ``pid`` the way pytest locks one."""
    path = root / "pytest-of-probe" / name
    path.mkdir(parents=True)
    (path / "payload").write_text("venv-ish bulk")
    if pid is not None:
        (path / ".lock").write_text(str(pid))
    return path


@pytest.fixture
def live_pid():
    """A real running process, reaped when the test ends."""
    proc = subprocess.Popen(["sleep", "60"], env=_ENV)
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


@pytest.fixture
def dead_pid() -> int:
    """A pid that has exited AND been reaped — no zombie row left behind."""
    proc = subprocess.Popen(["true"], env=_ENV)
    proc.wait()
    return proc.pid


@pytest.fixture
def sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A fake temp root with a worktree shell, a run dir, and a keeper."""
    root = tmp_path / "fake-tmp"
    wt = root / "ai-hats-wt-task-probe-XXXX"
    keep = root / "keep-me"
    for d in (wt, keep):
        d.mkdir(parents=True)
    (wt / "marker").write_text("leak")
    return root, wt, keep


def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file(), f"sweeper script missing: {SCRIPT}"


def test_dry_run_preserves_and_lists(sandbox, dead_pid) -> None:
    root, wt, keep = sandbox
    run = _run_dir(root, "pytest-7", pid=dead_pid)
    cp = _run(root, "--dry-run")
    assert cp.returncode == 0, cp.stderr
    assert wt.exists() and run.exists() and keep.exists()
    assert "ai-hats-wt-task-probe-XXXX" in cp.stdout
    assert "pytest-7" in cp.stdout
    assert "DRY-RUN" in cp.stdout
    assert "keep-me" not in cp.stdout


def test_default_reaps_the_provably_dead(sandbox, dead_pid) -> None:
    """No flag needed: the gate ran this on every push and freed nothing."""
    root, wt, keep = sandbox
    run = _run_dir(root, "pytest-7", pid=dead_pid)
    cp = _run(root)
    assert cp.returncode == 0, cp.stderr
    assert not wt.exists(), "unregistered ai-hats-wt-* shell not removed"
    assert not run.exists(), "run dir with an exited owner not removed"
    assert keep.exists(), "unrelated dir must survive"


def test_live_run_dir_survives(sandbox, live_pid) -> None:
    """The 29 GB case: a run dir whose pytest is still working stays put."""
    root, _wt, _keep = sandbox
    run = _run_dir(root, "pytest-8", pid=live_pid)
    cp = _run(root)
    assert cp.returncode == 0, cp.stderr
    assert run.exists(), "a run dir owned by a LIVE pytest must not be deleted"
    assert "LIVE" in cp.stdout


def test_pytest_of_root_is_never_a_candidate(sandbox, live_pid) -> None:
    """Judging the pytest-of-* root instead of its run dirs takes live runs too."""
    root, _wt, _keep = sandbox
    live = _run_dir(root, "pytest-8", pid=live_pid)
    cp = _run(root)
    assert cp.returncode == 0, cp.stderr
    assert live.parent.exists(), "the pytest-of-* root must survive as a container"


def test_unlocked_run_dir_is_left_to_pytest_then_forced(sandbox) -> None:
    """An unlocked dir is a clean exit's triage copy — pytest's keep=3 owns it."""
    root, _wt, _keep = sandbox
    run = _run_dir(root, "pytest-9", pid=None)
    assert _run(root).returncode == 0
    assert run.exists(), "an unlocked run dir must not be reaped by default"
    cp = _run(root, "--force")
    assert cp.returncode == 0, cp.stderr
    assert not run.exists(), "--force must take unlocked run dirs"


def test_reaps_a_read_only_dir_and_keeps_going(sandbox, dead_pid) -> None:
    """A mode-500 dir inside the tree must not stop the sweep at that dir.

    Measured on the real root: a venv-tier test left ``.venv/bin`` at
    ``dr-x------``, whose children cannot be unlinked. ``rm -rf`` failed, and
    under ``set -e`` that aborted the whole run — 22 of 36 dirs survived, 153
    of 167 GB unfreed (HATS-1624).
    """
    root, wt, _keep = sandbox
    stubborn = _run_dir(root, "pytest-11", pid=dead_pid)
    locked = stubborn / "venv" / "bin"
    locked.mkdir(parents=True)
    (locked / "python").write_text("#!/bin/sh\n")
    locked.chmod(0o500)
    later = _run_dir(root, "pytest-12", pid=dead_pid)

    try:
        cp = _run(root)
    finally:
        if locked.exists():
            locked.chmod(0o700)

    assert cp.returncode == 0, f"stderr:\n{cp.stderr}"
    assert not stubborn.exists(), "a read-only subdir must be chmod'ed and reaped"
    assert not later.exists(), "a later candidate must still be swept"


def test_unreadable_lock_is_not_proof(sandbox) -> None:
    root, _wt, _keep = sandbox
    run = root / "pytest-of-probe" / "pytest-10"
    run.mkdir(parents=True)
    (run / ".lock").write_text("not-a-pid\n")
    cp = _run(root)
    assert cp.returncode == 0, cp.stderr
    assert run.exists(), "a lock with no pid is not proof of death"


def test_is_idempotent(sandbox, dead_pid) -> None:
    root, _wt, _keep = sandbox
    _run_dir(root, "pytest-7", pid=dead_pid)
    first = _run(root)
    assert first.returncode == 0, first.stderr
    second = _run(root)
    assert second.returncode == 0, second.stderr
    assert "nothing to clean" in second.stdout


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@e.st", "-c", "user.name=t", *args],
        cwd=str(cwd),
        env={**_ENV, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def test_spares_a_registered_worktree(tmp_path: Path) -> None:
    """A LIVE worktree git still tracks survives; a pruned shell does not.

    The name is all the sweeper had to go on, so deleting by glob would have
    taken the 13 worktrees a developer had open (HATS-1624). Registration is
    the proof of reachability that separates them.
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
        env={"TMPDIR": str(root), **_ENV},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    assert live_wt.exists(), "the in-use worktree must NOT be deleted"
    assert "skip" in cp.stdout


def test_rejects_unknown_argument(tmp_path: Path) -> None:
    cp = _run(tmp_path, "--delete-everything")
    assert cp.returncode == 2, "an unknown flag must not fall through to a sweep"
