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

import hashlib
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

#: Housekeeping interval, NOT a consent window — an expiry would be the
#: supervisor's budget for thinking, since the mint precedes the answer
#: (HATS-1682). Validity rests on the four bindings below instead.
STALE_AFTER_SECONDS = 12 * 3600

#: The session that asked. Carried in the ticket and checked on the way out, so
#: a leftover from another session's question is not this session's consent.
SESSION_ENV = "AI_HATS_SESSION_ID"


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


def _prune(directory: Path, now: float) -> int:
    """Best-effort sweep of stale ticket files; how many went. Failure is not news."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    dropped = 0
    for path in entries:
        try:
            if path.suffix == ".json" and now - path.stat().st_mtime > STALE_AFTER_SECONDS:
                path.unlink()  # safe-delete: ok ephemeral-tmp
                dropped += 1
        except OSError:
            continue
    return dropped


def sweep(start: Path | None = None) -> int:
    """Clear stale tickets from the store under ``start``; how many went.

    ``mint`` and ``consume`` were the only sweeps, so a refused ticket lay on
    disk until the next gated transition — 1831s in a session that had none.
    The agent cannot tidy it either (``rm`` under the store is refused, and must
    be), which leaves the engine to call this once per session (HATS-1682).
    """
    directory = tickets_dir(start)
    return 0 if directory is None else _prune(directory, time.time())


def _session() -> str:
    return os.environ.get(SESSION_ENV, "")


def command_key(argv) -> str:
    """A stable name for ONE `rack` invocation, from its argv past the binary.

    Both halves can produce this and neither can produce the other's view: the
    hook has the command line and lexes it, the `rack` process has ``sys.argv``.
    Binding on the TOKENS rather than the raw line is what keeps the shell's own
    business — spacing, quote style, the ticket prefix the guard itself injects —
    from reading as a different command. ``argv[0]`` is left out: it is the
    resolved binary path on one side and whatever was typed on the other.
    """
    return hashlib.sha256("\x00".join(argv).encode("utf-8", "surrogateescape")).hexdigest()


def mint(
    task_id: str,
    *,
    start: Path | None = None,
    session_id: str | None = None,
    argv=(),
) -> str | None:
    """Issue a ticket for ``task_id`` and ``argv``; the nonce, or ``None``.

    ``None`` is not a failure to gate — the rack's own consent gate still runs
    and still refuses; it only means this transition has no question to ask.
    """
    directory = tickets_dir(start)
    if directory is None:
        return None
    now = time.time()
    nonce = secrets.token_hex(16)
    payload = json.dumps(
        {
            "task_id": task_id,
            "issued_at": now,
            "session_id": _session() if session_id is None else session_id,
            # The call the supervisor was shown. There is no callback after the
            # answer, so a ticket always outlives a "No" — binding it here is
            # what keeps that leftover from opening the NEXT call (HATS-1642).
            "command": command_key(argv),
        }
    )
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


def peek(
    task_id: str | None,
    *,
    start: Path | None = None,
    nonce: str | None = None,
    argv=(),
) -> bool:
    """Is consent for ``task_id`` on hand? Asking does not use it up.

    The gate runs early so its refusal is early, but the transition it guards
    can still be rolled back by a later subscriber — spending there would burn
    the supervisor's click on a move that never happened (HATS-1642).
    """
    return _valid_ticket(task_id, start=start, nonce=nonce, argv=argv) is not None


def consume(
    task_id: str | None,
    *,
    start: Path | None = None,
    nonce: str | None = None,
    argv=(),
) -> bool:
    """Spend the ticket naming ``task_id``; ``True`` iff THIS call spent it.

    One-shot by the unlink: whoever removes the file is the one call the ticket
    consented to.
    """
    path = _valid_ticket(task_id, start=start, nonce=nonce, argv=argv)
    if path is None:
        return False
    directory, spent = path.parent, _drop(path)
    if spent:
        # Sweep on use as well as on mint: a ticket nobody will spend reads
        # like live consent to anyone opening the directory.
        _prune(directory, time.time())
    return spent


def _valid_ticket(
    task_id: str | None,
    *,
    start: Path | None = None,
    nonce: str | None = None,
    argv=(),
):
    """The ticket file that consents to THIS call, here and now, or ``None``.

    Bound to the card, to the session, to the exact invocation, and one-shot —
    four axes because the mint precedes the answer, so every one of them is a
    way a ticket the supervisor refused could otherwise still be spent. None of
    them is the clock: an expiry would only fence the supervisor's thinking.

    ``task_id=None`` says the CARD axis is not part of the binding on this road,
    leaving three — session, exact argv, single use. That is the `ai-hats wt
    merge` road: it has no card to name, and its guard labels the question with
    the branch, which the CLI cannot reproduce (a branch left off the command
    line is labelled "this worktree" there and resolved to a real name here).
    Three axes still hold it: the branch, when typed, is INSIDE the argv the
    ticket is keyed on, and when omitted it is decided by a cwd the guard and
    the CLI share — so a ticket for another merge already fails on argv, and a
    rack ticket fails on it too (HATS-1682 A1).
    """  # comment-length: allow — why one road binds on three axes, not four
    raw = os.environ.get(TICKET_ENV, "") if nonce is None else nonce
    if not raw:
        return None
    if not NONCE_RE.match(raw):
        _note(f"ignoring a {TICKET_ENV} value that is not a minted nonce")
        return None
    directory = tickets_dir(start)
    if directory is None:
        return None
    path = directory / f"{raw}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _note(f"ticket already spent or never issued ({raw[:8]}…)")
        return None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        _note(f"unreadable ticket at {path}: {exc}")
        return None
    if not isinstance(data, dict):
        _note(f"ticket {raw[:8]}… is not a ticket")
        return None
    if task_id is not None and data.get("task_id") != task_id:
        # Another card's ticket: refuse WITHOUT spending it — it is still that
        # card's consent, and eating it here would send the supervisor back.
        _note(f"ticket {raw[:8]}… was issued for another task, not {task_id}")
        return None
    if data.get("session_id") != _session():
        # Same reason, other axis: another session's question, answered or not.
        _note(f"ticket {raw[:8]}… was issued to another session")
        return None
    if data.get("command") != command_key(argv):
        # The supervisor answered about a command, not about a card in general.
        _note(f"ticket {raw[:8]}… was issued for a different command")
        return None
    issued = data.get("issued_at")
    if not isinstance(issued, (int, float)) or isinstance(issued, bool):
        # Age no longer decides, but a ticket that cannot say when it was
        # written is corrupt rather than merely old.
        _note(f"ticket {raw[:8]}… carries no issue time")
        _drop(path)
        return None
    return path


def _drop(path: Path) -> bool:
    """Remove the ticket; ``True`` iff this call is the one that removed it."""
    try:
        path.unlink()  # safe-delete: ok ephemeral-tmp
    except OSError:
        return False
    return True
