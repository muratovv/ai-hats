#!/usr/bin/env python3
"""HATS-1899 — read a Bash command line the way the shell will run it.

Shared by the worktree guards on the Bash matcher, which need the same three
answers: where one command ends and the next begins, which directory a segment
actually runs in once a leading ``cd`` has moved, and what an inline ``PATH=``
sets. Extracted rather than copied — two walkers drift on the first fix, and
the shapes they disagree about are exactly the ones an agent uses by habit.

Owns no verdict: nothing here denies, nudges, or exits non-zero. Stdlib-only
(hooks run under the system interpreter, flattened into one directory).
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

#: Shell words that separate one command from the next.
SEPARATORS = ("&&", "||", ";", "|", "&")

ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream on shell separators into individual commands."""
    out: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            out.append([])
        else:
            out[-1].append(token)
    return [seg for seg in out if seg]


def strip_prefix(segment: list[str]) -> tuple[list[str], str | None]:
    """Drop leading ``VAR=value`` assignments and ``env``; return the PATH they set.

    The inline ``PATH=<worktree>/.venv/bin:$PATH <runner>`` form is a remedy the
    project itself recommends, so resolving through it is what keeps a guard off
    the back of a correct command."""
    path_override: str | None = None
    i = 0
    while i < len(segment):
        word = segment[i]
        if word == "env":
            i += 1
            continue
        if ENV_ASSIGN_RE.match(word):
            name, _, value = word.partition("=")
            if name == "PATH":
                path_override = os.path.expandvars(value)
            i += 1
            continue
        break
    return segment[i:], path_override


def leading_assignments(segment: list[str]) -> list[str]:
    """Names a segment SETS before its command word — ``env`` and ``export`` included.

    ``VAR=1 cmd``, a bare ``VAR=1``, ``export VAR=1`` and ``env VAR=1 cmd`` all bind
    VAR for what runs after them in the same shell, so a caller reasoning about what
    a line grants itself has to read all four alike. A ``VAR=1`` in ARGUMENT position
    (``echo VAR=1``) binds nothing and is deliberately not returned: that difference
    is the whole precision of a guard built on this."""
    names: list[str] = []
    for word in segment:
        if word in ("env", "export"):
            continue
        if ENV_ASSIGN_RE.match(word):
            names.append(word.partition("=")[0])
            continue
        break
    return names


def _tokens(command: str) -> list[str]:
    """Split a command line, treating punctuation as punctuation.

    ``shlex.split`` is whitespace-driven, so ``cd /x;git reset --hard`` is three
    words with ``/x;git`` in the middle: the line reads as one long ``cd`` and no
    command reaches the caller at all. A guard that denies must not be escapable
    by deleting a space, so the separators are tokenized rather than spelled."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def walk(command: str, cwd: Path) -> list[tuple[list[str], Path, str | None]] | None:
    """Each runnable segment with the directory it runs in and the PATH it sets.

    None means the line could not be read at all — unbalanced quotes, or a ``cd``
    whose destination was lost. A caller that went on to prove something about a
    command it could not parse would be guessing.

    The directory is tracked per segment rather than taken once from the payload,
    because a leading ``cd`` moves the agent inside the same call."""
    try:
        tokens = _tokens(command)
    except ValueError:
        return None

    out: list[tuple[list[str], Path, str | None]] = []
    effective_cwd = cwd
    for raw in segments(tokens):
        segment, path_override = strip_prefix(raw)
        if not segment:
            continue
        if segment[0] == "cd" and len(segment) > 1:
            try:
                effective_cwd = (effective_cwd / segment[1]).resolve()
            except (OSError, ValueError):
                return None
            continue
        out.append((segment, effective_cwd, path_override))
    return out
