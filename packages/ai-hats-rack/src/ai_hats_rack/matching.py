"""Shared glob matcher — the one pattern grammar for the v2 read surface
(HATS-1029): case-sensitive ``fnmatchcase`` globs (macOS/Linux agree). Callers
pass a SEQUENCE of globs with OR semantics (HATS-1032): a name matches if ANY
glob matches — no home-grown ``|`` DSL, the repeatable flag is the alternation.
Shared by prescription: ``context --with`` matches document names, ``ls --link``
matches link-kind names.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Callable, Sequence

#: A compiled predicate: ``matcher(name) -> bool``.
Matcher = Callable[[str], bool]


def compile_matcher(patterns: Sequence[str]) -> Matcher:
    """Compile ``patterns`` into a reusable OR-predicate over names.

    ``("plan*", "summary*")`` → matches ``plan.md`` and ``summary.md`` but not
    ``notes.md``. Blank globs are dropped; an empty/all-blank set compiles to a
    matcher that matches nothing (a loud empty result beats a silent match-all).
    """
    globs = tuple(p for raw in patterns if (p := raw.strip()))
    return lambda name: any(fnmatchcase(name, glob) for glob in globs)


def matches(patterns: Sequence[str], name: str) -> bool:
    """One-shot convenience: ``compile_matcher(patterns)(name)``."""
    return compile_matcher(patterns)(name)
