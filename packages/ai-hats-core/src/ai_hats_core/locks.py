"""Cross-process lock for read-modify-write sections (HATS-526).

``atomic_write_text`` (HATS-716) keeps each write torn-proof; ``locked_path``
adds mutual exclusion for the surrounding read-modify-write, which is
otherwise last-writer-wins. Lock files are safe to leave on disk (the kernel
releases ``flock`` on process death) but must live on a local filesystem —
``fcntl`` advisory locks are unreliable on NFS / SMB.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import filelock

DEFAULT_LOCK_TIMEOUT = 10.0  # seconds — guarded RMW sections run in <50ms


class LockTimeoutError(TimeoutError):
    """Raised when acquiring a ``locked_path`` lock exceeds its timeout."""


@contextmanager
def locked_path(path: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold an exclusive cross-process lock scoped to ``path``.

    Wrap the full read-modify-write of ``path`` — not just the write — so
    concurrent writers serialize instead of silently overwriting each other.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    try:
        lock.acquire()
    except filelock.Timeout as exc:
        raise LockTimeoutError(
            f"'{path}' is locked by another process for >{timeout:.0f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a stuck ai-hats process — check: ps aux | grep ai-hats\n"
            f"  If safe, remove the lock file and retry."
        ) from exc
    try:
        yield
    finally:
        lock.release()
