"""Session-owner liveness for the session-cache sweeps (HATS-1339 / S1).

Reap a session dir on **proof of death**, never on age alone — the TTL sweep
deleted 12 live maintainer sessions' skills and ``hooks.json`` mid-flight. The
owner comes from the anchor written here (``root_pid`` + OS ``start_time``,
reuse-proof), else from the pid in the dir name. The sweep runs on the
``create_session`` hot path over ~1300 candidates, so ONE ``ps`` reads the whole
process table into :class:`LivenessSnapshot` and queries hit memory.
Leaf: stdlib + ``ai_hats_core``, importable from ``environment_recovery``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import functools
import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ai_hats_core import atomic_write_text

logger = logging.getLogger(__name__)

#: Inert to every session-cache reader: they all address named children
#: (``prompt.md``, ``plugin/``, ``rules/``, ``skills/``, ``hooks.json``), and the
#: sweep iterating ``sessions/`` only descends into directories.
ANCHOR_NAME = ".session-owner.json"

_PS_TIMEOUT_S = 5

#: The whole process table in one read — a ``ps`` per candidate would add ~1273
#: subprocesses to session start (plan Q4). ``state`` rides along free, and a
#: zombie's row is indistinguishable without it: same pid, original ``lstart``.
PS_TABLE_COMMAND = ("ps", "-A", "-o", "pid=,state=,lstart=")

#: ``ps`` state letter for a process that has exited and awaits its parent's
#: ``wait()``. Matched on the FIRST letter only — the column carries modifiers
#: (``Z+``), and only ``Z`` itself is the exited-not-reaped state.
_ZOMBIE_STATE = "Z"

#: One process keeping a session dir in use: its pid and the OS ``start_time``
#: recorded for it, or ``None`` where none could be read (no reuse detection).
Owner = tuple[int, str | None]


def anchor_path(cache_dir: Path) -> Path:
    """Where :func:`write_session_anchor` puts this session's owner record."""
    return cache_dir / ANCHOR_NAME


def _normalize(start_time: str) -> str:
    """Collapse ``ps`` column padding so the targeted and batched reads compare.

    ``ps -o lstart=`` pads the day-of-month (``Jun  9``) and the batched form
    adds a pid column; both sides go through here, so the stored baseline and
    the snapshot value are byte-comparable.
    """
    return " ".join(start_time.split())


def _proc_start_time(pid: int) -> tuple[bool, str | None]:
    """Targeted read for the anchor write, as ``(determined, value)``.

    ``(True, "<lstart>")`` the process exists; ``(True, None)`` ``ps`` ran and
    said no such process; ``(False, None)`` ``ps`` itself was unusable, which is
    never death. Same split as ``ownership._proc_start_time``.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ps unavailable for pid %s: %s", pid, exc)
        return (False, None)
    if out.returncode != 0:
        return (True, None)
    value = _normalize(out.stdout)
    return (True, value or None)


def write_session_anchor(cache_dir: Path) -> Path | None:
    """Record this process as the owner of ``cache_dir``; ``None`` if not written.

    Written before the dir is populated, so a sweep that races a starting
    session sees either the anchor or an empty dir the TTL cannot reach. A
    ``start_time`` of ``None`` (``ps`` unusable at write) costs reuse detection,
    not correctness — the pid alone still reads as live. The surface child is
    added later by :func:`record_surface_child`, once it has a pid.
    """
    pid = os.getpid()
    determined, start_time = _proc_start_time(pid)
    payload = {"root_pid": pid, "start_time": start_time if determined else None}
    target = anchor_path(cache_dir)
    try:
        atomic_write_text(target, json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        logger.warning("session-owner anchor not written to %s: %s", cache_dir, exc)
        return None
    return target


def record_surface_child(cache_dir: Path, pid: int) -> Path | None:
    """Add the surface CLI's pid to ``cache_dir``'s anchor; ``None`` if not added.

    The wrapper owns the dir on paper, but the process that READS it — plugin
    skills, ``settings.json``, ``hooks.json`` — is this child. On the sub-agent
    path the child is spawned over pipes with no controlling tty, so a SIGKILL
    of the wrapper hangs nothing up: a real ``agy`` was measured still running
    45s later, reparented to init, while the wrapper's death alone made the dir
    reapable. Recording the child makes the sweep keep the dir while EITHER
    lives. Its baseline is captured here for the same reason the wrapper's is —
    without it a reused child pid would pin the dir forever.
    """  # comment-length: allow — the measurement is why this second write exists
    target = anchor_path(cache_dir)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("surface child %s not recorded, anchor unusable: %s", pid, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("surface child %s not recorded, anchor at %s is not a record", pid, target)
        return None
    determined, start_time = _proc_start_time(pid)
    payload["child_pid"] = pid
    payload["child_start_time"] = start_time if determined else None
    try:
        atomic_write_text(target, json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        logger.warning("surface child %s not recorded in %s: %s", pid, cache_dir, exc)
        return None
    return target


@dataclass(frozen=True)
class LivenessSnapshot:
    """One ``ps`` read of the process table, queried in memory (plan Q4).

    ``available`` is False when ``ps`` could not be read at all — every query
    then degrades to ``os.kill`` rather than reading the empty table as a
    process-wide extinction event.
    """

    start_times: dict[int, str]
    available: bool
    zombies: frozenset[int] = field(default_factory=frozenset)

    @classmethod
    def capture(cls, *, command: Sequence[str] = PS_TABLE_COMMAND) -> LivenessSnapshot:
        """Read every pid's start time and state in one subprocess.

        ``command`` is the seam that lets the three unavailability branches be
        exercised against a real subprocess instead of a patched module.
        """
        try:
            out = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=_PS_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("process table unavailable, liveness falls back to os.kill: %s", exc)
            return cls(start_times={}, available=False)
        if out.returncode != 0:
            logger.warning(
                "ps exited %s, liveness falls back to os.kill: %s",
                out.returncode,
                out.stderr.strip(),
            )
            return cls(start_times={}, available=False)
        table: dict[int, str] = {}
        zombies: set[int] = set()
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            table[pid] = _normalize(parts[2]) if len(parts) > 2 else ""
            if parts[1].startswith(_ZOMBIE_STATE):
                zombies.add(pid)
        if not table:
            logger.warning("ps returned no parseable rows, liveness falls back to os.kill")
            return cls(start_times={}, available=False)
        return cls(start_times=table, available=True, zombies=frozenset(zombies))

    def is_live(self, root_pid: int | None, start_time: str | None) -> bool:
        """Whether the owner is still running. **Biased to True on uncertainty.**

        ``False`` only on proof: ``ps`` read the whole table and the pid is
        absent, present as a zombie (exited, awaiting its parent's ``wait()``),
        or present with a different ``start_time`` (reused). No ``root_pid``
        means no owner was resolved, which is not evidence of death; an unusable
        ``ps`` degrades to ``os.kill``, which never invents a death.
        """
        if not isinstance(root_pid, int) or root_pid <= 0:
            return True
        if not self.available:
            return _pid_alive(root_pid)
        if root_pid in self.zombies:
            return False
        current = self.start_times.get(root_pid)
        if current is None:
            return False
        if start_time is None:
            return True
        return current == _normalize(start_time)


def _pid_alive(pid: int) -> bool:
    """Is ``pid`` a RUNNING process? The per-pid gate, no subprocess, no table.

    ``os.kill(pid, 0)`` alone answers "the pid exists", which a zombie also
    does — so it is paired with :func:`_pid_is_zombie`, else an unreaped wrapper
    pins its cache dir for as long as its parent declines to reap it (where the
    pre-HATS-1339 TTL took it at 24h). No reuse detection either way: a reused
    pid reads as alive here and only the recorded baseline can part them.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    except OSError:
        return True  # uncertain → conservative keep
    return not _pid_is_zombie(pid)


@functools.cache
def _libproc() -> ctypes.CDLL | None:
    """Darwin's ``libproc``, or ``None`` where the zombie probe is unavailable."""
    if sys.platform != "darwin":
        return None
    name = ctypes.util.find_library("c")
    if name is None:
        logger.debug("libc not found, the darwin zombie probe is off")
        return None
    try:
        lib = ctypes.CDLL(name, use_errno=True)
        lib.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.proc_pidinfo.restype = ctypes.c_int
    except (OSError, AttributeError) as exc:
        logger.debug("libproc unusable, the darwin zombie probe is off: %s", exc)
        return None
    return lib


#: ``proc_pidinfo`` flavour + buffer for ``struct proc_bsdshortinfo``. A zombie
#: keeps its pid but no BSD proc record, so the call fails ESRCH — which is the
#: whole probe, since the caller already proved the pid exists.
_PROC_PIDT_SHORTBSDINFO = 13
_SHORTBSDINFO_SIZE = 64


def _pid_is_zombie(pid: int) -> bool:
    """Has ``pid`` exited without being reaped? **False on any doubt.**

    Called only for a pid ``os.kill`` just proved exists, and only ever
    downgrades alive to dead — so every unknown (an unsupported platform, an
    unreadable probe) answers False and the dir is kept, exactly as before.
    """
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        fields = stat.rpartition(")")[2].split()
        return bool(fields) and fields[0] == _ZOMBIE_STATE
    lib = _libproc()
    if lib is None:
        return False
    buf = ctypes.create_string_buffer(_SHORTBSDINFO_SIZE)
    ctypes.set_errno(0)
    written = lib.proc_pidinfo(pid, _PROC_PIDT_SHORTBSDINFO, 0, buf, _SHORTBSDINFO_SIZE)
    if written > 0:
        return False  # a real proc record ⇒ running
    return ctypes.get_errno() == errno.ESRCH


def _pid_from_dirname(name: str) -> int | None:
    """The pid a session id carries, matched against the id GRAMMAR.

    ``<YYYYMMDD>-<HHMMSS>-<counter>-<pid>``, sub-agents nesting as
    ``<parent>_<base>-<counter>-<pid>`` (``ai_hats_observe.session``): the
    innermost ``_`` component is the owning id, and it must fill all four
    positions. ``None`` for a name that carries no pid — ``dry-run``, and the
    reason this reads the grammar rather than the last dash token: the
    pre-HATS-1248 id ``<YYYYMMDD>-<HHMMSS>-<counter>`` ends at the counter,
    which read as pid 1 (launchd, never exits — the dir and its whole project
    key were then retained forever) or as some unrelated pid the session never
    owned.
    """  # comment-length: allow — the shape IS the contract this parser enforces
    parts = name.rpartition("_")[2].split("-")
    if len(parts) != 4:
        return None
    day, clock, counter, pid = parts
    if not (len(day) == 8 and day.isdigit() and len(clock) == 6 and clock.isdigit()):
        return None
    if not (counter.isdigit() and pid.isdigit()):
        return None
    value = int(pid)
    return value if value > 0 else None


def session_owners(session_dir: Path) -> tuple[Owner, ...]:
    """Every process whose life keeps ``session_dir`` in use — wrapper first.

    ``()`` = nobody could be resolved (no anchor, no pid in the name) and the
    caller must fall back to TTL. One entry = the wrapper alone, which is what
    an anchor written before HATS-1339's ``child_pid`` field carries and what a
    dir-name pid can ever say; a missing field is a silent reader, never a
    death. An unreadable anchor degrades to the dir-name pid with no baseline —
    that reads as live while the process exists, so corrupt bytes never become a
    death signal.
    """  # comment-length: allow — one sentence per arity the caller can get
    target = anchor_path(session_dir)
    if target.is_file():
        owners = _read_anchor(target)
        if owners is not None:
            return owners
    root_pid = _pid_from_dirname(session_dir.name)
    return () if root_pid is None else ((root_pid, None),)


def _read_anchor(target: Path) -> tuple[Owner, ...] | None:
    """Parse one anchor file; ``None`` (reported) when it is unusable."""
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("session-owner anchor unreadable at %s: %s", target, exc)
        return None
    pid = data.get("root_pid") if isinstance(data, dict) else None
    if not isinstance(pid, int) or pid <= 0:
        logger.warning("session-owner anchor at %s carries no root_pid", target)
        return None
    start_time = data.get("start_time")
    owners: list[Owner] = [(pid, start_time if isinstance(start_time, str) else None)]
    child = data.get("child_pid")
    if isinstance(child, int) and child > 0:
        child_start = data.get("child_start_time")
        owners.append((child, child_start if isinstance(child_start, str) else None))
    return tuple(owners)
