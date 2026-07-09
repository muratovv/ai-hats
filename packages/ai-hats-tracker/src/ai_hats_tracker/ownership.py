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
def _proc_start_time(pid: int) -> tuple[bool, str | None]:
    """OS start time of ``pid`` as ``(determined, value)`` — errors must not read
    as death, so "no such process" and "couldn't tell" stay distinct:
    ``(True, "<lstart>")`` exists; ``(True, None)`` ps ran, gone → dead;
    ``(False, None)`` ps errored → undetermined (caller must not reclaim).
    ``ps -o lstart=`` (POSIX); the raw string is stored/compared verbatim.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return (False, None)  # ps unavailable → undetermined
    if out.returncode != 0:
        return (True, None)  # ps ran, no such process → dead
    return (True, out.stdout.strip())  # exists (string, possibly empty)


def _capture_start_time(pid: int) -> str | None:
    """The ``start_time`` to store at claim time, or ``None`` if unknown."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    determined, value = _proc_start_time(pid)
    return value if determined else None


def _pid_alive(pid: int) -> bool:
    """Conservative ``os.kill(pid, 0)`` fallback when ``ps`` is unavailable — no
    reuse detection (a reused pid reads as alive); biased to keep the claim."""
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
    """Whether ``record``'s owner process is still alive.

    ``record`` = a registry entry ``{session_id, root_pid, start_time, ...}``.
    ``True`` = owner alive → task NOT reclaimable; ``False`` = certainly dead →
    safe to reclaim/sweep. **Biased to True on uncertainty**: a transient ``ps``
    failure must never read as death (else a neighbour's task gets stolen).
    ``False`` only on proof — no ``root_pid``, ``ps`` says no such process, or a
    reused pid (``start_time`` mismatch). ``ps`` unavailable ⇒ ``os.kill``.
    """
    pid = record.get("root_pid")
    if not isinstance(pid, int) or pid <= 0:
        return False  # no anchor was ever captured → not a live claim
    determined, current = _proc_start_time(pid)
    if not determined:
        return _pid_alive(pid)  # ps unavailable → conservative os.kill
    if current is None:
        return False  # ps ran, process gone → certainly dead
    recorded = record.get("start_time")
    if recorded is None:
        return True  # process exists, no baseline to compare → assume live
    return current == recorded


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
    is_live: LiveFn = record_is_live,
) -> None:
    """Claim ``task_id`` for ``session_id``, or raise :class:`OwnershipRefused`.

    Under the registry lock: sweep dead records, then refuse if a live *other*
    session owns the task (reclaim guard) or if this session already holds a
    different task (single-slot). Ownership is never forcibly overridden — a live
    owner is respected; a stuck one is reclaimed only once its process dies.
    """
    with _lock(path):
        reg = _load(path)
        owners = {t: r for t, r in reg["owners"].items() if is_live(r)}

        current = owners.get(task_id)
        if current is not None and current.get("session_id") != session_id:
            raise OwnershipRefused(
                task_id, reason="held by a live agent", holder=current.get("session_id", "")
            )
        others = [t for t, r in owners.items() if r.get("session_id") == session_id and t != task_id]
        if others:
            raise OwnershipRefused(
                task_id, reason=f"session already holds {sorted(others)}; finish it first"
            )

        owners[task_id] = {
            "session_id": session_id,
            "root_pid": root_pid,
            "start_time": _capture_start_time(root_pid),
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
    without importing any liveness primitive.

    Read under the lock: ``filelock`` has no shared/read mode, so a plain
    exclusive acquire serialises against a concurrent ``take``/``release`` and
    never returns a torn or already-superseded owner.
    """
    with _lock(path):
        record = _load(path)["owners"].get(task_id)
    if record is None:
        return None
    return {**record, "is_live": is_live(record)}


def held_by(path: Path, session_id: str) -> list[str]:
    """Task ids currently recorded for ``session_id`` (single-slot check).

    No liveness filter: the calling session is alive by definition and a record
    with its id was written by this run, so presence == held. Read under the lock
    for the same reason as ``owner_of``.
    """
    with _lock(path):
        owners = _load(path)["owners"]
    return sorted(t for t, r in owners.items() if r.get("session_id") == session_id)


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
