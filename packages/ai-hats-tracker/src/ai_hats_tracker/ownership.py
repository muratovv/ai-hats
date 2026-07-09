"""Task-ownership registry — one serialized JSON file + one lock (HATS-955).

Lets a second agent safely reclaim a task left mid-flight: detect abandonment,
and guarantee the previous owner can't silently re-take it. Keyed by task id;
every op reads the whole registry under the lock, sweeps dead records, decides in
RAM, atomic-writes. Liveness = reclaim-on-certain-death (owner ``root_pid`` + OS
``start_time``, reuse-proof), no TTL, single-host; the inline liveness helpers
deliberately copy ``version_refs`` (one consumer). Full rationale: HATS-955 plan.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_hats_core import atomic_write_text
from filelock import FileLock

_SCHEMA_VERSION = 1


class OwnershipRefused(Exception):
    """A claim was refused: a live *other* agent owns the task, or the caller
    already holds another unreleased task (single-slot). ``force=True`` bypasses
    the refusal. Carries structured fields for the CLI to render."""

    def __init__(self, task_id: str, *, reason: str, holder: str = "") -> None:
        self.task_id = task_id
        self.reason = reason
        self.holder = holder
        super().__init__(f"Ownership refused for {task_id}: {reason}")


# --------------------------------------------------------------------------- #
# Inline liveness (deliberate copy of version_refs; see module docstring).
# --------------------------------------------------------------------------- #
def _proc_start_time(pid: int) -> str | None:
    """OS-reported start time of ``pid`` as an opaque string, or ``None``.

    ``ps -o lstart=`` (POSIX; macOS + Linux, second precision). ``None`` = no
    such process **or** ``ps`` unavailable; the raw string is stored and compared
    verbatim, never parsed.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _pid_alive(pid: int) -> bool:
    """Conservative ``os.kill(pid, 0)`` fallback when ``start_time`` is
    unavailable — no reuse detection (a reused pid reads as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    except OSError:
        return True  # uncertain → conservative keep
    return True


def record_is_live(record: dict) -> bool:
    """True iff the process that claimed ``record`` is still alive.

    Precise path: recorded and current ``start_time`` both known and equal ⇒ same
    process ⇒ live; differ ⇒ pid reused ⇒ dead. Fallback (``ps`` unavailable):
    ``os.kill`` liveness. A record without an integer ``root_pid`` (e.g. a
    harness-less run that never captured one) protects nothing ⇒ dead.
    """
    pid = record.get("root_pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    recorded = record.get("start_time")
    current = _proc_start_time(pid)
    if recorded is not None and current is not None:
        return current == recorded
    return _pid_alive(pid)


LiveFn = Callable[[dict], bool]


# --------------------------------------------------------------------------- #
# Registry I/O
# --------------------------------------------------------------------------- #
def _lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


def _load(path: Path) -> dict:
    """Load ``{"version": N, "owners": {task_id: record}}``.

    A missing / unreadable / malformed file protects nothing → treated as an
    empty registry (conservative: everything reclaimable), never raised.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _SCHEMA_VERSION, "owners": {}}
    if not isinstance(data, dict) or not isinstance(data.get("owners"), dict):
        return {"version": _SCHEMA_VERSION, "owners": {}}
    return data


def _save(path: Path, reg: dict) -> None:
    reg["version"] = _SCHEMA_VERSION
    atomic_write_text(path, json.dumps(reg, indent=2, sort_keys=True))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Public ops
# --------------------------------------------------------------------------- #
def take(
    path: Path,
    task_id: str,
    session_id: str,
    root_pid: int,
    *,
    force: bool = False,
    is_live: LiveFn = record_is_live,
) -> None:
    """Claim ``task_id`` for ``session_id``. Raises :class:`OwnershipRefused`
    unless ``force``.

    Under the registry lock: sweep dead records, then refuse if a live *other*
    session owns the task (reclaim guard) or if this session already holds a
    different task (single-slot). ``force`` skips the refusals but still records
    the claim, so a force-executed task is never left unowned.
    """
    with _lock(path):
        reg = _load(path)
        owners = {t: r for t, r in reg["owners"].items() if is_live(r)}

        current = owners.get(task_id)
        if not force:
            if current is not None and current.get("session_id") != session_id:
                raise OwnershipRefused(
                    task_id, reason="held by a live agent", holder=current.get("session_id", "")
                )
            others = [t for t, r in owners.items() if r.get("session_id") == session_id and t != task_id]
            if others:
                raise OwnershipRefused(
                    task_id, reason=f"session already holds {sorted(others)}; stop/close it first"
                )

        owners[task_id] = {
            "session_id": session_id,
            "root_pid": root_pid,
            "start_time": _proc_start_time(root_pid) if isinstance(root_pid, int) and root_pid > 0 else None,
            "claimed_at": _now(),
        }
        reg["owners"] = owners
        _save(path, reg)


def release(path: Path, task_id: str, session_id: str) -> None:
    """Drop ``task_id``'s record iff owned by ``session_id`` (explicit ``stop``).

    No-op if the registry file does not exist (a harness-less run never wrote
    one) or the task is owned by a different session.
    """
    if not path.exists():
        return
    with _lock(path):
        reg = _load(path)
        current = reg["owners"].get(task_id)
        if current is not None and current.get("session_id") == session_id:
            del reg["owners"][task_id]
            _save(path, reg)


def finish(path: Path, task_id: str) -> None:
    """Unconditionally drop ``task_id``'s record (close side-effect). No-op if
    the registry file does not exist or the task is unowned."""
    if not path.exists():
        return
    with _lock(path):
        reg = _load(path)
        if task_id in reg["owners"]:
            del reg["owners"][task_id]
            _save(path, reg)


def owner_of(path: Path, task_id: str, *, is_live: LiveFn = record_is_live) -> dict | None:
    """Return ``task_id``'s owner record augmented with ``is_live: bool``, or
    ``None`` if unowned. Liveness is computed here so CLI callers render it
    without importing any liveness primitive."""
    reg = _load(path)
    record = reg["owners"].get(task_id)
    if record is None:
        return None
    return {**record, "is_live": is_live(record)}


def held_by(path: Path, session_id: str) -> list[str]:
    """Task ids currently recorded for ``session_id`` (single-slot check).

    No liveness filter: the calling session is alive by definition and a record
    with its id was written by this run, so presence == held.
    """
    reg = _load(path)
    return sorted(t for t, r in reg["owners"].items() if r.get("session_id") == session_id)


def sweep(path: Path, *, is_live: LiveFn = record_is_live) -> int:
    """Drop records whose owner is dead. Returns the count removed. No-op if the
    file is absent."""
    if not path.exists():
        return 0
    with _lock(path):
        reg = _load(path)
        live = {t: r for t, r in reg["owners"].items() if is_live(r)}
        removed = len(reg["owners"]) - len(live)
        if removed:
            reg["owners"] = live
            _save(path, reg)
        return removed
