"""Liveness refs for the versioned-install layout (HATS-649 / R2).

A run that executes from a managed ``versions/<sha>/`` venv writes a ref tying
its OS process to the sha it pinned. The orphan-version reclaim
(:func:`ai_hats.version_recovery.reclaim_orphan_versions`) keeps a version alive
iff a **live** ref points to it — liveness decided by ``root_pid`` + the
OS-reported process ``start_time`` (reuse-proof on a single host). Refs are
written at the ``create_session`` chokepoint and cleaned (when dead) by the same
reclaim pass, so they never leak.

**Reclaim-on-certain-death, no TTL** (HATS-649 supervisor decision): a reused
pid has a different OS ``start_time`` than the one recorded at write, so a dead
run is classified as dead *with certainty* — no time-based backstop is needed on
a single host. Cross-host / shared-FS coordination is out of scope (a ref's
``root_pid`` is meaningless on another host).

Stdlib only — process start_time via ``ps -o lstart=`` (POSIX; macOS + Linux),
with an ``os.kill`` liveness fallback when ``ps`` is unavailable (degrades to
no-reuse-detection, but stays conservative: keeps disk, never deletes a live
env).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .paths import _is_safe_sha_component, versions_root

# One run_id per process — stable across repeated ``create_session`` calls
# (multi-turn sub-agent, wrap), so the ref file (keyed by ``root_pid``) is
# idempotently refreshed rather than multiplied. Generated lazily so importing
# this module is side-effect-free.
_PROCESS_RUN_ID: str | None = None


def _process_run_id() -> str:
    global _PROCESS_RUN_ID
    if _PROCESS_RUN_ID is None:
        _PROCESS_RUN_ID = uuid.uuid4().hex
    return _PROCESS_RUN_ID


def refs_dir(project_dir: Path) -> Path:
    """Liveness-ref directory: ``<ai_hats_dir>/versions/.refs/``."""
    return versions_root(project_dir) / ".refs"


def current_run_sha(project_dir: Path) -> str | None:
    """Managed ``sha`` THIS process runs from, derived from ``sys.prefix``.

    Returns the version dir name when the running interpreter's prefix is
    ``versions/<sha>/``; ``None`` for the legacy ``.venv``, an editable / dev
    checkout, or any prefix outside ``versions/``. Those runs pin no managed
    version, so they write no ref — and R2 never reclaims the legacy ``.venv``
    (its reclaim is HATS-653).
    """
    try:
        prefix = Path(sys.prefix).resolve()
        vroot = versions_root(project_dir).resolve()
        rel = prefix.relative_to(vroot)
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) == 1 and _is_safe_sha_component(parts[0]):
        return parts[0]
    return None


def _proc_start_time(pid: int) -> str | None:
    """OS-reported start time of ``pid`` as an opaque string, or ``None``.

    Uses ``ps -o lstart= -p <pid>`` (stable within a host; second precision).
    ``None`` means "no such process" **or** ``ps`` unavailable — callers treat
    the two cases via the ``os.kill`` fallback in :func:`ref_is_live`. The raw
    ``ps`` string is stored and compared verbatim; we never parse it.
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
    """Conservative liveness fallback when ``start_time`` is unavailable.

    No reuse detection — a reused pid reads as alive. Used only when ``ps`` is
    absent on either the write or the check side; errs toward keeping disk.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    except OSError:
        return True  # uncertain → conservative keep
    return True


def ref_is_live(ref: dict) -> bool:
    """True iff the run that wrote ``ref`` is still alive.

    Precise path: both the recorded and the current ``start_time`` are known and
    equal ⇒ same process ⇒ live; they differ ⇒ pid reused ⇒ dead. Fallback
    (``ps`` unavailable on either side): ``os.kill`` liveness. A malformed ref
    (no integer ``root_pid``) protects nothing and is treated as dead.
    """
    pid = ref.get("root_pid")
    if not isinstance(pid, int):
        return False
    recorded = ref.get("start_time")
    current = _proc_start_time(pid)
    if recorded is not None and current is not None:
        return current == recorded
    return _pid_alive(pid)


def write_current_run_ref(project_dir: Path) -> Path | None:
    """Write/refresh this run's liveness ref; no-op (``None``) for legacy runs.

    Keyed by ``root_pid`` so repeated ``create_session`` calls in one process
    idempotently refresh a single file. Atomic (tmp + ``replace``). Returns the
    ref path, or ``None`` when this process pins no managed version.
    """
    sha = current_run_sha(project_dir)
    if sha is None:
        return None
    pid = os.getpid()
    d = refs_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    ref = {
        "run_id": _process_run_id(),
        "root_pid": pid,
        "start_time": _proc_start_time(pid),
        "sha": sha,
    }
    dest = d / f"{pid}.json"
    tmp = d / f".{pid}.json.tmp"
    tmp.write_text(json.dumps(ref), encoding="utf-8")
    tmp.replace(dest)
    return dest


def load_refs(project_dir: Path) -> list[tuple[Path, dict]]:
    """Load all ref files as ``[(path, dict)]``; skip unreadable / malformed.

    A ref that can't be parsed into a dict protects nothing — it's skipped here
    and swept by the reclaim pass when its sha is reclaimed. Hidden temp files
    (``.<pid>.json.tmp``) and non-``.json`` entries are ignored.
    """
    d = refs_dir(project_dir)
    if not d.exists():
        return []
    out: list[tuple[Path, dict]] = []
    for entry in sorted(d.iterdir()):
        if entry.name.startswith(".") or entry.suffix != ".json":
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((entry, data))
    return out
