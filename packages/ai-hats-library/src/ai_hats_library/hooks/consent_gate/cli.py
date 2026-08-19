#!/usr/bin/env python3
"""`consent` — the verb a PERSON types to open a window (ADR-0029 D3, D4).

Positional grammar, no punctuation, because the place it is typed is a chat
shell escape with no completion and no hints:

    consent                      every declared type, default window
    consent all 30               the same, 30 minutes
    consent rack.transition 30   one operation type
    consent HATS-1734 30         one subject, any declared type

Run by the agent instead of the human it is not a hole: PreToolUse fires on the
agent's commands and not on a person's `!` escape, so the agent's attempt turns
into the question it was going to have to ask anyway (D12).
"""  # comment-length: allow — the grammar has no other home the typist will read

from __future__ import annotations

import sys
import time

from . import host
from .issue import DEFAULT_WINDOW_MINUTES, IssueError, Radius, issue

#: Exit codes. Distinct on purpose: "there was nobody to issue against" is not
#: a refusal, and a caller that cannot tell them apart repeats the wrong fix.
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NO_SESSION = 2

#: The word that means "every declared type" rather than a subject of that name.
ALL = "all"


def parse(argv: list[str]) -> tuple[str, int]:
    """``(what, minutes)`` from the positional grammar. Raises :class:`IssueError`."""
    args = list(argv)
    minutes = DEFAULT_WINDOW_MINUTES
    if args and args[-1].isdigit():
        minutes = int(args.pop())
    if len(args) > 1:
        raise IssueError(f"expected `consent [<what>] [<minutes>]`, got: {' '.join(argv)}")
    return (args[0] if args else ALL), minutes


def radius_for(what: str, declared: tuple[str, ...]) -> Radius:
    """Turn one positional word into a radius, against what the role declared.

    A type nobody declared is refused rather than written: a grant for an
    unreachable type would sit in the store looking like consent and open
    nothing (HATS-1735).
    """
    if not declared:
        raise IssueError(
            "this session's role declares no gateable operation types — "
            "nothing to consent to (see `apps.consent_gate` in the role)"
        )
    if what == ALL:
        return Radius(types=tuple(declared))
    if what in declared:
        return Radius(types=(what,))
    if "." in what:
        raise IssueError(
            f"{what!r} is not a declared operation type — declared: {', '.join(declared)}"
        )
    return Radius(types=tuple(declared), subjects=(what,))


def describe(radius: Radius, expires_at: float) -> str:
    """What the human reads back. Never the grant id — this goes to the model's context."""
    left = max(0, int(round((expires_at - time.time()) / 60)))
    subjects = "" if radius.subjects == ("*",) else f", subject {', '.join(radius.subjects)}"
    return (
        f"consent granted for {', '.join(radius.types)}{subjects} — "
        f"{left} minutes, until {time.strftime('%H:%M:%S', time.localtime(expires_at))}"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    identity = host.envelope()
    if not identity or not identity.get("id"):
        print(
            "consent: no ai-hats session here — a grant is bound to one, so there is "
            "nothing to bind it to. Run this inside the session you want to open.",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION
    root = host.store_root(identity)
    if root is None:
        print(
            "consent: this session publishes no cache dir, so the store cannot be "
            "located. Restart the session on a current ai-hats.",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION
    try:
        what, minutes = parse(args)
        radius = radius_for(what, host.policy(identity))
        grant = issue(
            radius,
            store_root=root,
            session_id=str(identity.get("id")),
            project_dir=identity.get("project_dir") or "",
            minutes=minutes,
            label=" ".join(args),
            issued_via="verb",
        )
    except IssueError as exc:
        print(f"consent: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(describe(radius, grant.expires_at))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
