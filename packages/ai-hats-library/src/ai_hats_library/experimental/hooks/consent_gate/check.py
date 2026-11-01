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
import stat
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence

#: Bumped when the on-disk grant shape changes. Both halves read the same file,
#: so a grant from another version is ignored rather than guessed at.
GRANT_VERSION = 1

#: Where the store sits under the session's cache dir, and the grants under it.
STORE_DIRNAME = "consent"
GRANTS_DIRNAME = "grants"

#: Bits that must be clear on the store and on every grant in it. A credential
#: anyone but its owner can reach is IGNORED, not warned about — ssh-add refuses
#: identity files others can read, sudoers refuses a group-writable stamp dir.
PRIVATE_MASK = 0o077


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
    #: What the grant that answered allowed, for the record to name. Scalars, not
    #: the raw dict: a dict here would alias the loaded grant and make a frozen
    #: value unhashable — frozen that holds only when nobody looks (HATS-1736).
    radius: tuple[str, ...] = ()
    issued_at: float = 0.0
    expires_at: float = 0.0

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
    """Type only. The SUBJECT axis is deliberately absent (HATS-1735).

    Narrowing to one card would have to read the id out of a command line the
    guard sees before the shell expands it — measured: a loop over `$id` hands
    the gate the literal token. Should per-operation narrowing be wanted, it
    arrives as a selector owned by the external operation adapter.
    """
    types = tuple(radius.get("types") or ())
    return any(fnmatch.fnmatch(op.type, str(pattern)) for pattern in types)


def _report(sink: "Callable[[str], None] | None", message: str) -> None:
    """Hand a refusal to the caller's collector; silence here would hide it."""
    if sink is not None:
        sink(message)


def mode_refusal(path: Path) -> str:
    """Why ``path`` may not be trusted, or ``""`` when only its owner can reach it.

    ``lstat``, never ``stat``: following a link would judge the mode of whatever
    it points AT, so a link could carry a grant whose bits we never inspected.
    A grant is a regular file the owner wrote; a link in its place is refused.
    """
    try:
        info = path.lstat()
    except OSError as exc:
        # Reachable for real: a file unlinked between `iterdir` and here.
        return f"permissions unreadable: {exc}"
    if stat.S_ISLNK(info.st_mode):
        return "permissions refused: a symlink is not a grant"
    mode = stat.S_IMODE(info.st_mode)
    if mode & PRIVATE_MASK:
        return f"permissions {oct(mode)[-3:]} — reachable beyond its owner"
    return ""


def _load(path: Path, on_reject: Callable[[str], None] | None = None) -> dict | None:
    """One grant file, or ``None`` when it is not one we can trust."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Said out loud: "your grant file is corrupt" must not read as "you have
        # no grant" — the reader would go looking in the wrong place (HATS-1736).
        _report(on_reject, f"grant {path.stem[:8]} is unreadable ({exc})")
        return None
    return data if isinstance(data, dict) and data.get("v") == GRANT_VERSION else None


def _as_float(value: object) -> float:
    """A timestamp off disk, or 0.0 — a corrupt one must not raise mid-scan."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def live_grants(
    *,
    store_root: Path,
    session_id: str,
    project_dir: Path,
    now: float,
    on_reject: "Callable[[str], None] | None" = None,
) -> list[dict]:
    """Every unexpired grant of THIS session for THIS project, newest first.

    Bound on three axes, and argv is not among them: the grant is written before
    the command exists, so there is nothing to key it on (ADR-0029 D1).
    """
    found: list[dict] = []
    anchor = str(Path(project_dir))
    directory = grants_dir(store_root)
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []  # no store == no grant; never an error (P5)
    except OSError as exc:
        # Not "there is no store" — "the store is there and would not open".
        # Collapsing the two hid a chmod 000 behind "no live grant covers this".
        _report(on_reject, f"the grant store could not be read ({exc})")
        return []
    # The root as well as `grants/` — anyone who can write the root can replace
    # `grants/` wholesale, so checking only the child stops one level short.
    loose = mode_refusal(Path(store_root)) or mode_refusal(directory)
    if loose:
        # One loose directory disarms everything under it: anyone who can write
        # there can plant a grant, so the files inside prove nothing.
        _report(on_reject, f"the grant store is ignored ({loose})")
        return []
    for entry in entries:
        if entry.suffix != ".json":
            continue
        loose = mode_refusal(entry)
        if loose:
            _report(on_reject, f"grant {entry.stem[:8]} is ignored ({loose})")
            continue
        data = _load(entry, on_reject)
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
    rejected: list[str] = []
    live = live_grants(
        store_root=store_root,
        session_id=session_id,
        project_dir=project_dir,
        now=moment,
        on_reject=rejected.append,
    )
    for grant in live:
        if _covers(grant.get("radius") or {}, op):
            return Verdict(
                Outcome.GRANTED,
                "",
                str(grant.get("id", "")),
                tuple(str(item) for item in ((grant.get("radius") or {}).get("types") or ())),
                _as_float(grant.get("issued_at")),
                _as_float(grant.get("expires_at")),
            )
    if live:
        radii = "; ".join(
            ", ".join(str(t) for t in (g.get("radius") or {}).get("types") or ()) for g in live
        )
        reason = f"a grant is live but its radius does not name {op.type!r} (covers: {radii})"
    else:
        reason = "no live grant covers this operation"
    # A grant refused for its mode is the likeliest explanation of a refusal the
    # reader did not expect, so it rides in the text they are already reading.
    if rejected:
        reason = f"{reason}; {'; '.join(rejected)}"
    return Verdict(Outcome.DENIED, reason)


def _now() -> float:
    """Wall clock, isolated so a caller can pin it and a test can move it."""
    import time

    return time.time()


def store_root_from(session_cache_dir: str | os.PathLike | None) -> Path | None:
    """``<session cache dir>/consent`` — the one place both halves look."""
    if not session_cache_dir:
        return None
    return Path(session_cache_dir) / STORE_DIRNAME


# --- the record: one line per USE, never per check (ADR-0029 D11) --------------

#: The engine's own journal, beside the grants. A host that has its own supplies
#: a sink instead; this is what the engine writes when it ships alone (HATS-1740).
JOURNAL_FILENAME = "journal.jsonl"


def journal_entry(verdict: Verdict, op: Operation, *, now: float) -> dict:
    """The record itself: which grant, which operation, and how wide it reached."""
    expires_at, issued_at = verdict.expires_at, verdict.issued_at
    return {
        "ts": now,
        "event": "consent.spend",
        "outcome": verdict.outcome.value,
        "grant_id": verdict.grant_id,
        "op": op.type,
        "subject": op.subject,
        "label": op.label,
        "radius": list(verdict.radius),
        "window_s": max(0, int(expires_at - issued_at)),
        "left_s": max(0, int(expires_at - now)),
    }


def file_journal(store_root: Path) -> Callable[[Mapping[str, object]], bool]:
    """The default sink: append-only JSONL beside the grants, owner-only."""

    def _write(entry: Mapping[str, object]) -> bool:
        path = Path(store_root) / JOURNAL_FILENAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(handle, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            print(f"[consent] NOT RECORDED ({entry.get('op')}) — {path}: {exc}", file=sys.stderr)
            return False
        return True

    return _write


def record(
    verdict: Verdict,
    op: Operation,
    *,
    store_root: Path | None = None,
    journal: Callable[[Mapping[str, object]], bool] | None = None,
    now: float | None = None,
) -> bool:
    """Write the ONE line saying a grant paid for ``op``. Never called on a peek.

    Only ``GRANTED`` is a use. A refusal sends the caller to another channel that
    writes its own record, so counting both would make one operation look like
    two — which is what the guard's own hatch line did until HATS-1736 (P6).

    The session wrapper records immediately before spawning the protected
    command. A failed record therefore refuses the launch instead of producing
    an unaudited authorization use.
    """
    if verdict.outcome is not Outcome.GRANTED:
        return False
    entry = journal_entry(verdict, op, now=float(now) if now is not None else _now())
    sink = journal if journal is not None else (file_journal(store_root) if store_root else None)
    if sink is None:
        print(f"[consent] NOT RECORDED ({op.type}) — no journal and no store", file=sys.stderr)
        return False
    return bool(sink(entry))
