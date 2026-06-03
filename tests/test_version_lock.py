"""Unit tests for the version GC/acquire lock wrapper (HATS-650 / R3).

These exercise the *wrapper* contract — path, parent ``mkdir``, and the
``filelock.Timeout → VersionLockError`` translation. The cross-process
crash-safety property (kernel auto-release on ``kill -9``) is proven by the
real-binary e2e; here we only confirm same-process contention surfaces our
domain error and that the lock is released when the context exits.
"""

from __future__ import annotations

from pathlib import Path

import filelock
import pytest

from ai_hats.version_lock import (
    VersionLockError,
    gc_lock_path,
    versions_lock,
)


def test_gc_lock_path_points_at_versions_dotlock(tmp_path: Path) -> None:
    assert gc_lock_path(tmp_path) == (
        tmp_path / ".agent" / "ai-hats" / "versions" / ".gc.lock"
    )


def test_acquire_creates_parent_dir_and_releases(tmp_path: Path) -> None:
    # versions/ does not exist yet — the wrapper must mkdir it (filelock won't).
    assert not gc_lock_path(tmp_path).parent.exists()
    with versions_lock(tmp_path, timeout=1.0):
        assert gc_lock_path(tmp_path).parent.is_dir()
    # After the context exits the lock is free → a fresh acquire succeeds fast.
    with versions_lock(tmp_path, timeout=1.0):
        pass


def test_contention_raises_version_lock_error(tmp_path: Path) -> None:
    # Hold the underlying lock via a raw filelock handle, then assert our
    # wrapper raises the DOMAIN error (not a bare filelock.Timeout) on timeout.
    gc_lock_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    holder = filelock.FileLock(str(gc_lock_path(tmp_path)), timeout=1.0)
    holder.acquire()
    try:
        with pytest.raises(VersionLockError) as exc:
            with versions_lock(tmp_path, timeout=0.2):
                pass
        # Message names the lock file so a stuck operator can find it.
        assert ".gc.lock" in str(exc.value)
    finally:
        holder.release()


def test_lock_reacquirable_after_clean_release(tmp_path: Path) -> None:
    for _ in range(3):
        with versions_lock(tmp_path, timeout=1.0):
            pass  # serial re-acquire must never deadlock against itself
