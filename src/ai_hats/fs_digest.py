"""Content digest over a directory tree — a stdlib leaf (HATS-1217).

Lifted out of ``plugin_dir`` so the materialization port does not depend on the
legacy-mirror sweep: the hash is shared by the port, the sweeper and that sweep,
and belongs to none of them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def dir_digest(root: Path) -> str:
    """sha256 over sorted (relpath, bytes) — equal digests ⇔ equal trees."""
    if not root.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
