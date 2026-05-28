"""e2e: scripts/clean-tmp-cruft.sh (HATS-570).

Real-bash exercise of the one-shot sweep helper. Falls under the
maintainer e2e gate (the change touches ``scripts/*.sh``): a real
``bash`` invocation of the real script, asserting the dry-run / --force /
idempotency contract. Fails-under-revert — delete the script and the
first run errors on the missing path.
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


def test_never_deletes_cwd_worktree(tmp_path: Path) -> None:
    """A worktree dir the caller is standing in must be skipped."""
    root = tmp_path / "fake-tmp"
    live_wt = root / "ai-hats-wt-task-live-YYYY"
    live_wt.mkdir(parents=True)
    cp = subprocess.run(
        ["bash", str(SCRIPT), "--force"],
        cwd=str(live_wt),  # stand INSIDE the worktree
        env={"TMPDIR": str(root), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True, text=True, timeout=30,
    )
    assert cp.returncode == 0, cp.stderr
    assert live_wt.exists(), "the in-use worktree must NOT be deleted"
    assert "skip" in cp.stdout
