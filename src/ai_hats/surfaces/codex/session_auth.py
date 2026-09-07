"""Reconcile file credentials without resurrecting an older session's login."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_hats_core.atomic_io import atomic_write_bytes
from filelock import FileLock

from ai_hats.materialization import Materializer

_BASELINE = ".ai-hats-auth-baseline.json"


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def stage_auth(base: Path, session: Path, port: Materializer) -> None:
    with port.lock(base / ".ai-hats" / "auth.lock"):
        data = _read(base / "auth.json")
        if data is not None:
            port.write_private_text(session / "auth.json", data.decode("utf-8"))
        port.write_private_text(session / _BASELINE, json.dumps({"digest": _digest(data)}))


def reconcile_auth(base: Path, session: Path) -> str | None:
    baseline = _read(session / _BASELINE)
    if baseline is None:
        return None
    expected = json.loads(baseline)["digest"]
    local_path = session / "auth.json"
    shared_path = base / "auth.json"
    if local_path.is_symlink() or shared_path.is_symlink():
        raise RuntimeError("Refusing to reconcile symlinked Codex credentials")
    local = _read(local_path)
    if _digest(local) == expected:
        return None
    with FileLock(str(base / ".ai-hats" / "auth.lock"), timeout=10):
        shared = _read(shared_path)
        if shared == local:
            return None
        if _digest(shared) != expected:
            return "Codex credentials changed in another session; local auth changes retained"
        if local is None:
            shared_path.unlink(missing_ok=True)  # safe-delete: ok propagate explicit session logout
        else:
            atomic_write_bytes(shared_path, local, mode=0o600)
    return None
