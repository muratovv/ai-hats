"""Canonical atomic-write helper: unique-tmp-in-same-dir + ``os.replace``.

A crash or kill mid-write can never leave a torn or truncated target: the
target path always reflects either the complete old bytes or the complete new
bytes. This consolidates the half-dozen ad-hoc ``tmp + replace`` conventions
that had accreted across the codebase (``hypothesis/io._atomic_dump``,
``safe_delete._write_atomic``, ``cli/maintenance._flip_current``,
``worktree_locks._atomic_write_json``, ``version_refs``, ``cli/hyp.py``,
``assembler._atomic_write_if_changed``) into one primitive (HATS-716).

Design notes:

* **Unique tmp name** (via :func:`tempfile.mkstemp` in the *target's own*
  directory) makes the write safe under concurrent writers — two processes
  writing the same target never collide on the tmp file, which the prior
  deterministic ``<name>.tmp`` conventions could. The tmp must live in the
  target's directory so the final ``os.replace`` is a same-filesystem rename
  (atomic on POSIX and Windows); the system temp dir is the wrong filesystem.
* **Umask-default perms** are preserved when ``mode`` is ``None``: ``mkstemp``
  creates ``0o600``, so we re-apply ``0o666 & ~umask`` to match what
  ``open(path, "w")`` would have produced — migrating callers must not
  silently tighten file permissions.
* **Durability scope** is *process* crash/kill, not power loss: ``os.replace``
  gives atomicity, but we do not ``fsync``. The data here (backlog cards,
  config, session metrics) is regenerable, not a write-ahead log, and none of
  the conventions this replaces fsync'd either.
"""

import os
import tempfile
from pathlib import Path


def _umask_perms() -> int:
    """Return the perms ``open(path, "w")`` would create under the current umask."""
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Atomically write ``data`` to ``path``.

    Args:
        path: Target file. Parent directories are created if missing.
        data: Bytes to write.
        mode: Optional octal permission bits applied to the tmp file *before*
            the rename, so the final path never appears with default-umask
            perms (HATS-467). When ``None``, umask-default perms are applied
            (matching ``open(path, "w")``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode if mode is not None else _umask_perms())
        os.replace(tmp, path)
    except BaseException:
        # Leave no orphan tmp behind on any failure (incl. KeyboardInterrupt).
        try:
            tmp.unlink()  # safe-delete: ok ephemeral atomic-write tmp (never user data)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    """Atomically write ``text`` to ``path``. Thin wrapper over :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)
