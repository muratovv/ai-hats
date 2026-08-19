#!/usr/bin/env python3
"""The CHECKING half of the consent gate — stdlib only, ai-hats free (HATS-1735).

Reachable from a stdlib-only PreToolUse hook as a flattened sibling, the way
``consent_ticket`` already is, so it may not import anything of ours.

It answers one question — does a live grant cover THIS operation — and answers it
with one of four outcomes, never a bool: "I could not look" must not read as
"refused", which is the collapse ADR-0029 D6 exists to forbid.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

#: Bumped when the on-disk grant shape changes. Both halves read the same file,
#: so a grant from another version is ignored rather than guessed at.
GRANT_VERSION = 1

#: Where the store sits under the session's cache dir, and the grants under it.
STORE_DIRNAME = "consent"
GRANTS_DIRNAME = "grants"


class Outcome(Enum):
    """Why the gate answered as it did. A CLOSED set (ADR-0029 D6).

    ``DISMISSED`` is produced by no road in this slice and is reserved anyway:
    collapsing "the human said no" into "refused" is what made a refusal
    indistinguishable from a question nobody asked.
    """

    GRANTED = "granted"
    DENIED = "denied"
    NO_AGENT = "no-agent"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class Operation:
    """What is about to happen, as three strings the engine compares but never parses.

    ``type`` and ``subject`` are opaque here on purpose (ADR-0029 D2): the day
    this module learns what a card is, a third operation type stops being data.
    """

    type: str
    subject: str = ""
    params: Mapping[str, str] = field(default_factory=dict)
    label: str = ""


@dataclass(frozen=True)
class Verdict:
    """The answer, plus the sentence a human needs to act on it."""

    outcome: Outcome
    reason: str = ""
    grant_id: str = ""

    def __bool__(self) -> bool:
        """Refuse truth-testing: `if verdict:` is the collapse D6 forbids."""
        raise TypeError(
            "Verdict has four outcomes and no truth value — compare "
            "`verdict.outcome is Outcome.GRANTED` (ADR-0029 D6)."
        )


def grants_dir(store_root: Path) -> Path:
    """Where grants live under a store root."""
    return Path(store_root) / GRANTS_DIRNAME


def _covers(radius: Mapping[str, object], op: Operation) -> bool:
    types = tuple(radius.get("types") or ())
    subjects = tuple(radius.get("subjects") or ("*",))
    if not any(fnmatch.fnmatch(op.type, str(pattern)) for pattern in types):
        return False
    return any(fnmatch.fnmatch(op.subject, str(pattern)) for pattern in subjects)


def _load(path: Path) -> dict | None:
    """One grant file, or ``None`` when it is not one we can trust."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # unreadable/corrupt == no grant on this file; the caller counts it
    return data if isinstance(data, dict) and data.get("v") == GRANT_VERSION else None


def live_grants(*, store_root: Path, session_id: str, project_dir: Path, now: float) -> list[dict]:
    """Every unexpired grant of THIS session for THIS project, newest first.

    Bound on three axes, and argv is not among them: the grant is written before
    the command exists, so there is nothing to key it on (ADR-0029 D1).
    """
    found: list[dict] = []
    anchor = str(Path(project_dir))
    try:
        entries = sorted(grants_dir(store_root).iterdir())
    except OSError:
        return []  # no store == no grant; never an error (P5)
    for entry in entries:
        if entry.suffix != ".json":
            continue
        data = _load(entry)
        if data is None:
            continue
        if data.get("session_id") != session_id or data.get("project_dir") != anchor:
            continue
        try:
            if float(data.get("expires_at", 0)) <= now:
                continue
        except (TypeError, ValueError):
            continue
        found.append(data)
    found.sort(key=lambda g: float(g.get("expires_at", 0)), reverse=True)
    return found


def check(
    op: Operation,
    *,
    session_id: str | None,
    store_root: Path | None,
    project_dir: Path | None,
    policy: Sequence[str],
    now: float | None = None,
) -> Verdict:
    """Does a live grant cover ``op``?

    ``project_dir`` is the anchor of the operation, ALREADY RESOLVED by the host
    — this module walks no tree. Measured (HATS-1735): two resolvers in this
    repo disagree about a linked worktree, so re-resolving here would refuse a
    grant for a reason that has nothing to do with consent.

    ``policy`` is what the ROLE declared as gateable. Undeclared type -> DENIED,
    and the caller falls back to whatever road it had before.
    """
    if not session_id or store_root is None or project_dir is None:
        return Verdict(Outcome.NO_AGENT, "no session envelope — the store cannot be located")
    if not any(fnmatch.fnmatch(op.type, str(declared)) for declared in policy):
        declared = ", ".join(sorted(str(p) for p in policy)) or "nothing"
        return Verdict(
            Outcome.DENIED, f"{op.type!r} is not declared as gateable (declared: {declared})"
        )
    moment = float(now) if now is not None else _now()
    live = live_grants(
        store_root=store_root, session_id=session_id, project_dir=project_dir, now=moment
    )
    for grant in live:
        if _covers(grant.get("radius") or {}, op):
            return Verdict(Outcome.GRANTED, "", str(grant.get("id", "")))
    if live:
        radii = "; ".join(
            ", ".join(str(t) for t in (g.get("radius") or {}).get("types") or ()) for g in live
        )
        return Verdict(
            Outcome.DENIED,
            f"a grant is live but its radius does not name {op.type!r} (covers: {radii})",
        )
    return Verdict(Outcome.DENIED, "no live grant covers this operation")


def _now() -> float:
    """Wall clock, isolated so a caller can pin it and a test can move it."""
    import time

    return time.time()


def store_root_from(session_cache_dir: str | os.PathLike | None) -> Path | None:
    """``<session cache dir>/consent`` — the one place both halves look."""
    if not session_cache_dir:
        return None
    return Path(session_cache_dir) / STORE_DIRNAME
