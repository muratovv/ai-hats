#!/usr/bin/env python3
"""The ISSUING half of the consent gate — written by a PERSON, never by us.

No point in ai-hats mints a grant (ADR-0029 D10): a grant written at the moment
of a question would outlive a "No" and open the next call. The only writer is the
`consent` verb a human types.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .check import GRANT_VERSION, GRANTS_DIRNAME

#: Default window, in minutes. A supervisor's dial — edit it right here; why 120
#: rather than polkit's 5 is HATS-1735 (our unit of work is an agent session).
DEFAULT_WINDOW_MINUTES = 120

#: A window nobody can mean. Zero would write a grant already dead.
MIN_WINDOW_MINUTES = 1


@dataclass(frozen=True)
class Radius:
    """How wide the grant reaches. Window-only — there is no budget axis (D5)."""

    types: tuple[str, ...]
    subjects: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class Grant:
    """A written grant, as the issuer needs to describe it back to the human."""

    id: str
    path: Path
    radius: Radius
    issued_at: float
    expires_at: float


class IssueError(Exception):
    """The grant was not written, and the human is told exactly why."""


def issue(
    radius: Radius,
    *,
    store_root: Path,
    session_id: str,
    project_dir: Path,
    minutes: int = DEFAULT_WINDOW_MINUTES,
    label: str = "",
    issued_via: str = "",
    now: float | None = None,
) -> Grant:
    """Write one grant and return it. Raises :class:`IssueError` rather than half-writing."""
    if not radius.types:
        raise IssueError("a grant with an empty radius would cover nothing")
    if minutes < MIN_WINDOW_MINUTES:
        raise IssueError(f"a window of {minutes} minutes is already over")
    moment = float(now) if now is not None else time.time()
    expires_at = moment + minutes * 60
    grant_id = secrets.token_hex(16)
    directory = Path(store_root) / GRANTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{grant_id}.json"
    payload = json.dumps(
        {
            "v": GRANT_VERSION,
            "id": grant_id,
            "session_id": session_id,
            # The anchor the checking half compares against, so a wide grant
            # cannot reach a repository the session does not govern.
            "project_dir": str(Path(project_dir)),
            "label": label,
            "issued_at": moment,
            "expires_at": expires_at,
            "radius": {"types": list(radius.types), "subjects": list(radius.subjects)},
            "issued_via": issued_via,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return Grant(id=grant_id, path=path, radius=radius, issued_at=moment, expires_at=expires_at)
