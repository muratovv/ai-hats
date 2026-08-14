#!/usr/bin/env python3
"""HATS-1642 — one-shot supervisor-consent tickets for ``plan → execute``.

Two readers, ONE file: ``safety_gate.py`` imports it as a hook sibling (so it
stays stdlib-only), the integrator as ``ai_hats_library.hooks.consent_ticket``,
to wire :func:`consume` into the rack's plan-consent gate. A second copy would
be a mirror of the nonce, the directory and the clock — one that drifts.

Trust model, stated not implied: an agent that reads the disk reads these files
too. The ticket removes the corner-cut, not a determined agent (HATS-1613).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

#: The env name the ticket rides on into the ``rack`` process. Not a hatch the
#: agent may set: ``safety_gate.SELF_GRANT_FORBIDDEN`` refuses the inline form,
#: so the only writer is the hook that asked the supervisor.
TICKET_ENV = "AI_HATS_CONSENT_TICKET"

#: The only shape :func:`consume` will look up. An env value is caller-shaped
#: input, and ``../../../etc/passwd`` must never become a path.
NONCE_RE = re.compile(r"\A[0-9a-f]{32}\Z")

#: How long a minted ticket stays valid. Long enough for a supervisor to read
#: the plan before clicking, short enough that yesterday's click is not consent.
TTL_SECONDS = 900


def _git_common_dir(start: Path) -> Path | None:
    """The git dir shared by a checkout and every worktree linked to it.

    Resolved by walking, not by shelling out: a PreToolUse hook runs on every
    Bash call and a subprocess per call is a tax the gate does not need.
    """
    try:
        start = start.resolve()
    except OSError:
        return None
    for d in (start, *start.parents):
        dot = d / ".git"
        try:
            if dot.is_dir():
                return dot
            if not dot.is_file():
                continue
            text = dot.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.split(":", 1)[1].strip())
        parts = gitdir.parts
        if "worktrees" in parts:
            gitdir = Path(*parts[: parts.index("worktrees")])
        try:
            return gitdir.resolve()
        except OSError:
            return None
    return None


def tickets_dir(start: Path | None = None) -> Path | None:
    """Where tickets live: beside the bypass journal, in the common git dir."""
    common = _git_common_dir(start if start is not None else Path.cwd())
    return None if common is None else common / "ai-hats" / "consent"


def _note(message: str) -> None:
    """Never raise, never go quiet — an unexplained refusal is the worse bug."""
    print(f"[consent-ticket] {message}", file=sys.stderr)


def _prune(directory: Path, now: float) -> None:
    """Best-effort sweep of ticket files past their TTL. Failure is not news."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.suffix == ".json" and now - path.stat().st_mtime > TTL_SECONDS:
                path.unlink()  # safe-delete: ok ephemeral-tmp
        except OSError:
            continue


def mint(task_id: str, *, start: Path | None = None, session_id: str = "") -> str | None:
    """Issue a ticket for ``task_id``; the nonce, or ``None`` when it cannot.

    ``None`` is not a failure to gate — the rack's own consent gate still runs
    and still refuses; it only means this transition has no question to ask.
    """
    directory = tickets_dir(start)
    if directory is None:
        return None
    now = time.time()
    nonce = secrets.token_hex(16)
    payload = json.dumps({"task_id": task_id, "issued_at": now, "session_id": session_id})
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _prune(directory, now)
        fd = os.open(directory / f"{nonce}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except OSError as exc:
        _note(f"ticket for {task_id} NOT minted — {directory}: {exc}")
        return None
    return nonce


def consume(
    task_id: str,
    *,
    start: Path | None = None,
    nonce: str | None = None,
    now: float | None = None,
) -> bool:
    """Spend the ticket naming ``task_id``; ``True`` iff THIS call spent it.

    One-shot by the unlink: whoever removes the file is the one call the ticket
    consented to. Bound to the card, so another card's ticket is not this card's
    consent, and expiring, so a click is consent now and not tomorrow.
    """
    raw = os.environ.get(TICKET_ENV, "") if nonce is None else nonce
    if not raw:
        return False
    if not NONCE_RE.match(raw):
        _note(f"ignoring a {TICKET_ENV} value that is not a minted nonce")
        return False
    directory = tickets_dir(start)
    if directory is None:
        return False
    path = directory / f"{raw}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _note(f"ticket already spent or never issued ({raw[:8]}…)")
        return False
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        _note(f"unreadable ticket at {path}: {exc}")
        return False
    if not isinstance(data, dict) or data.get("task_id") != task_id:
        # Another card's ticket: refuse WITHOUT spending it — it is still that
        # card's consent, and eating it here would send the supervisor back.
        _note(f"ticket {raw[:8]}… was issued for another task, not {task_id}")
        return False
    issued = data.get("issued_at")
    if not isinstance(issued, (int, float)) or isinstance(issued, bool):
        _note(f"ticket {raw[:8]}… carries no issue time")
        _drop(path)
        return False
    if (time.time() if now is None else now) - issued > TTL_SECONDS:
        _note(f"ticket {raw[:8]}… expired after {TTL_SECONDS}s — ask again")
        _drop(path)
        return False
    return _drop(path)


def _drop(path: Path) -> bool:
    """Remove the ticket; ``True`` iff this call is the one that removed it."""
    try:
        path.unlink()  # safe-delete: ok ephemeral-tmp
    except OSError:
        return False
    return True
