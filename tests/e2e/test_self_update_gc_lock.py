"""E2E: cleanup never deadlocks and never corrupts after a hard kill (HATS-650 / R3).

Two crash-safety properties of the ``versions/.gc.lock`` advisory lock, both with
a real launcher + real pip + real ``ai-hats self update`` (per ``dev_rule_e2e_gate``):

- :func:`test_e2e_gc_lock_crash_safe_auto_release` — an install is frozen mid-
  critical-section (``.complete`` written, ``current`` not yet flipped) **holding
  the lock**, then ``SIGKILL``-ed. The kernel auto-releases the ``fcntl`` lock on
  death, so the next ``self update`` re-acquires and converges. Fail-under-revert
  anchor: while the install is paused the test asserts the lock is **held** (a
  ``filelock`` probe times out); reverting the lock makes that probe succeed.

- :func:`test_e2e_gc_lock_serializes_complete_flip_window` — while the install is
  frozen between ``.complete`` and the flip, a concurrent GC pass (the real
  ``EnvironmentRecovery`` collaborator every session runs) tries to reclaim the
  just-completed, non-``current``, unreferenced target. The lock makes it skip;
  the install then flips ``current`` onto a **live** dir. Fail-under-revert:
  without the lock the concurrent GC reclaims the target out from under the flip,
  so ``current`` ends up pointing at a deleted dir.

The freeze point is the ``AI_HATS_TEST_PAUSE_AFTER_COMPLETE`` seam in
``_run_managed_versioned_update`` — no flaky SIGKILL/timing race; the test drives
the interleaving deterministically via the ``.ready`` sentinel.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import filelock
import pytest

pytestmark = pytest.mark.pip_heavy  # HATS-678: real pip at call time → capped via conftest.PIP_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-650 e2e\n")
    _git(["add", marker], src_repo)
    _git(["commit", "--quiet", "-m", f"test: advance HEAD ({marker})"], src_repo)
    return _head_sha(src_repo)


def _wait_for(path: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.02)


def _bootstrap(tmp_path: Path):
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    _git(["config", "user.email", "e2e@test"], src_repo)
    _git(["config", "user.name", "E2E"], src_repo)
    sha_a = _head_sha(src_repo)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    versions = project / ".agent" / "ai-hats" / "versions"
    assert versions.joinpath("current").read_text().strip() == sha_a
    return env, src_repo, launcher_dest, project, versions, sha_a


@pytest.mark.integration
def test_e2e_gc_lock_crash_safe_auto_release(tmp_path: Path) -> None:
    env, src_repo, launcher_dest, project, versions, sha_a = _bootstrap(tmp_path)
    sha_b = _advance(src_repo, "E2E_R3_A.txt")
    assert sha_b != sha_a

    gate = tmp_path / "pause_gate"
    ready = Path(str(gate) + ".ready")
    lock_path = versions / ".gc.lock"

    paused_env = {**env, "AI_HATS_TEST_PAUSE_AFTER_COMPLETE": str(gate)}
    proc = subprocess.Popen(
        [str(launcher_dest), "self", "update"],
        cwd=str(project), env=paused_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Installer reaches the freeze point: sha_b complete, current still sha_a,
        # holding the lock between .complete and the flip.
        _wait_for(ready, timeout=300)
        assert (versions / sha_b / ".complete").exists()
        assert (versions / "current").read_text().strip() == sha_a

        # Fail-under-revert anchor: the install MUST hold the lock here. With the
        # lock reverted this acquire succeeds and the raises-block fails.
        with pytest.raises(filelock.Timeout):
            filelock.FileLock(str(lock_path), timeout=0.5).acquire()

        # Hard-kill the holder mid-critical-section.
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # The kernel released the fcntl lock on death — a fresh acquire succeeds
    # immediately (a delete-on-exit lockfile would still be present → deadlock).
    probe = filelock.FileLock(str(lock_path), timeout=10)
    probe.acquire()
    probe.release()

    # And the next real self update converges: re-acquires, reuses the complete
    # sha_b, flips current. No manual cleanup, no wedged lock.
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    assert (versions / "current").read_text().strip() == sha_b
    assert (versions / sha_b / ".complete").exists()


@pytest.mark.integration
def test_e2e_gc_lock_serializes_complete_flip_window(tmp_path: Path) -> None:
    env, src_repo, launcher_dest, project, versions, sha_a = _bootstrap(tmp_path)
    sha_b = _advance(src_repo, "E2E_R3_B.txt")
    assert sha_b != sha_a

    gate = tmp_path / "pause_gate"
    ready = Path(str(gate) + ".ready")
    versioned_python = str(versions / sha_a / "bin" / "python")

    paused_env = {**env, "AI_HATS_TEST_PAUSE_AFTER_COMPLETE": str(gate)}
    upd = subprocess.Popen(
        [str(launcher_dest), "self", "update"],
        cwd=str(project), env=paused_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(ready, timeout=300)
        # sha_b complete, current still sha_a, no live ref pins sha_b, and the
        # installer holds the lock in the .complete→flip window.
        assert (versions / sha_b / ".complete").exists()
        assert (versions / "current").read_text().strip() == sha_a

        # A concurrent GC pass — the real EnvironmentRecovery collaborator every
        # session runs at create_session, invoked from the installed versioned
        # venv. Under R3 it cannot take the held lock → skips → sha_b survives.
        # Reverted (no lock) it reclaims sha_b out from under the pending flip.
        gc_snippet = (
            "from pathlib import Path;"
            "from ai_hats.environment_recovery import EnvironmentRecovery;"
            f"EnvironmentRecovery(Path(r'{project}')).run()"
        )
        _run([versioned_python, "-c", gc_snippet], cwd=project, env=env, timeout=60)

        # Release the install → it flips current → sha_b and exits.
        gate.touch()
        out, err = upd.communicate(timeout=300)
        assert upd.returncode == 0, f"paused update failed:\n{out}\n{err}"
    finally:
        if upd.poll() is None:
            upd.kill()
            upd.wait()

    # The flip landed on a LIVE, complete dir — the lock kept the concurrent GC
    # from reclaiming sha_b in the .complete→flip window.
    current = (versions / "current").read_text().strip()
    assert current == sha_b
    assert (versions / current).is_dir(), "current points at a reclaimed dir (corruption)"
    assert (versions / current / ".complete").exists()
