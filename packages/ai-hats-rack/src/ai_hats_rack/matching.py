"""Shared glob matcher — the one pattern grammar for the v2 read surface
(HATS-1029): ``fnmatchcase`` globs joined by ``|`` alternation
(``plan*|summary*``, ``*.log``), case-sensitive so macOS/Linux agree. Shared by
prescription: ``context --with`` matches document names, ``ls --link`` matches
link-kind names.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Callable

#: A compiled predicate: ``matcher(name) -> bool``.
Matcher = Callable[[str], bool]


def alternatives(pattern: str) -> tuple[str, ...]:
    """The non-empty, stripped ``|``-separated globs of a pattern."""
    return tuple(alt for raw in pattern.split("|") if (alt := raw.strip()))


def compile_matcher(pattern: str) -> Matcher:
    """Compile ``pattern`` into a reusable predicate over names.

    ``plan*|summary*`` → matches ``plan.md`` and ``summary.md`` but not
    ``notes.md``. An all-empty pattern compiles to a matcher that matches
    nothing (a loud empty result beats a silent match-all).
    """
    globs = alternatives(pattern)
    return lambda name: any(fnmatchcase(name, glob) for glob in globs)


def matches(pattern: str, name: str) -> bool:
    """One-shot convenience: ``compile_matcher(pattern)(name)``."""
    return compile_matcher(pattern)(name)
