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

import json
import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core import atomic_write_text

logger = logging.getLogger(__name__)

#: Inert to every session-cache reader: they all address named children
#: (``prompt.md``, ``plugin/``, ``rules/``, ``skills/``, ``hooks.json``), and the
#: sweep iterating ``sessions/`` only descends into directories.
ANCHOR_NAME = ".session-owner.json"

_PS_TIMEOUT_S = 5

#: The whole process table in one read — a ``ps`` per candidate would add ~1273
#: subprocesses to session start (plan Q4).
PS_TABLE_COMMAND = ("ps", "-A", "-o", "pid=,lstart=")


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
    not correctness — the pid alone still reads as live.
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


@dataclass(frozen=True)
class LivenessSnapshot:
    """One ``ps`` read of the process table, queried in memory (plan Q4).

    ``available`` is False when ``ps`` could not be read at all — every query
    then degrades to ``os.kill`` rather than reading the empty table as a
    process-wide extinction event.
    """

    start_times: dict[int, str]
    available: bool

    @classmethod
    def capture(cls, *, command: Sequence[str] = PS_TABLE_COMMAND) -> LivenessSnapshot:
        """Read every pid's start time in one subprocess.

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
        for line in out.stdout.splitlines():
            pid_text, _, rest = line.strip().partition(" ")
            if pid_text.isdigit():
                table[int(pid_text)] = _normalize(rest)
        if not table:
            logger.warning("ps returned no parseable rows, liveness falls back to os.kill")
            return cls(start_times={}, available=False)
        return cls(start_times=table, available=True)

    def is_live(self, root_pid: int | None, start_time: str | None) -> bool:
        """Whether the owner is still running. **Biased to True on uncertainty.**

        ``False`` only on proof: ``ps`` read the whole table and the pid is
        absent, or the pid is present with a different ``start_time`` (reused).
        No ``root_pid`` means no owner was resolved, which is not evidence of
        death; an unusable ``ps`` degrades to ``os.kill``, which cannot detect
        reuse but never invents a death.
        """
        if not isinstance(root_pid, int) or root_pid <= 0:
            return True
        if not self.available:
            return _pid_alive(root_pid)
        current = self.start_times.get(root_pid)
        if current is None:
            return False
        if start_time is None:
            return True
        return current == _normalize(start_time)


def _pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` fallback for an unusable ``ps`` — no reuse detection
    (a reused pid reads as alive); biased to keeping the dir."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    except OSError:
        return True  # uncertain → conservative keep
    return True


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


def session_owner(session_dir: Path) -> tuple[int | None, str | None]:
    """The ``(root_pid, start_time)`` owning ``session_dir``, anchor first.

    ``(None, None)`` = no owner could be resolved (no anchor, no pid in the
    name) and the caller must fall back to TTL. An unreadable anchor degrades to
    the dir-name pid with no baseline — that reads as live while the process
    exists, so corrupt bytes never become a death signal.
    """
    target = anchor_path(session_dir)
    if target.is_file():
        anchor = _read_anchor(target)
        if anchor is not None:
            return anchor
    return (_pid_from_dirname(session_dir.name), None)


def _read_anchor(target: Path) -> tuple[int, str | None] | None:
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
    return (pid, start_time if isinstance(start_time, str) else None)
