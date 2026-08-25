"""HATS-1816 — the argv corpus both readers of a guarded verb are judged on.

Rows are DATA: the differential test asserts the two readers agree on each, and
the baseline test asserts no verdict moved. `source` is the card's state when the
move is attempted — explicit, never inferred, because `plan->execute` fires only
from `plan` (HATS-1813) and a guessed source silently changes the question.
"""

from __future__ import annotations

from typing import NamedTuple


class Row(NamedTuple):
    surface: str
    argv: tuple[str, ...]
    source: str | None
    note: str = ""


CORPUS: tuple[Row, ...] = (
    # --- rack.transition, plain shapes
    Row("rack", ("transition", "HATS-1", "execute"), "plan"),
    Row("rack", ("transition", "HATS-1", "done"), "review"),
    Row("rack", ("transition", "HATS-1", "plan"), "brainstorm", "not a guarded target"),
    Row("rack", ("transition", "HATS-1", "--state", "execute"), "plan"),
    # a note ABOUT the move must not read as the move (HATS-1682)
    Row("rack", ("transition", "HATS-1", "--log", "execute"), "plan", "log text, not a move"),
    Row("rack", ("transition", "HATS-1", "--log", "done"), "review", "log text, not a move"),
    # --force carries no exemption
    Row("rack", ("transition", "HATS-1", "done", "--force", "--reason", "x"), "review"),
    # options BEFORE the positional id — the gate skips flag values, the wrapper does not
    Row("rack", ("transition", "--state", "execute", "HATS-1"), "plan", "flag before id"),
    Row("rack", ("transition", "--tasks-dir", "/t", "HATS-1", "execute"), "plan", "flag before id"),
    # read verbs move nothing
    Row("rack", ("ls",), None),
    Row("rack", ("context", "HATS-1"), None),
    Row("rack", ("create", "title"), None),
    # --- wt.merge
    Row("ai-hats", ("wt", "merge", "task/x"), None),
    Row("ai-hats", ("wt", "merge"), None, "branch detected from cwd"),
    Row("ai-hats", ("wt", "merge", "task/x", "--accept-drift"), None),
    # REAL global options, and they take a VALUE: measured on master, both
    # readers went silent on these — a merge into master with no question.
    Row("ai-hats", ("--provider", "claude", "wt", "merge", "task/x"), None, "value flag first"),
    Row("ai-hats", ("-r", "maintainer", "wt", "merge", "task/x"), None, "short value flag"),
    Row("ai-hats", ("--provider=claude", "wt", "merge", "task/x"), None, "inline value"),
    # neighbours that must stay quiet
    Row("ai-hats", ("wt", "list"), None),
    Row("ai-hats", ("wt", "create", "task/x"), None),
    Row("ai-hats", ("wt", "status"), None),
    # not declared today — must read as no-match until HATS-1804
    Row("ai-hats", ("wt", "discard", "task/x"), None, "undeclared until HATS-1804"),
    Row("ai-hats", ("self", "update"), None, "undeclared until HATS-1804"),
)
