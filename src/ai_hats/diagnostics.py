"""One value type for a user-facing problem, and one place its text is spelled.

HATS-1753. A product module states the problem — level, what, which file, what
fixes it — and stops there; which channel carries it is the caller's decision.
Zero internal imports on purpose: composition modules import this, and a cycle
would be the one thing that keeps them printing to stderr instead.
"""  # comment-length: allow — the zero-import property is the contract

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["Level", "Diagnostic", "emit_to_stderr"]


class Level(str, Enum):
    NOTE = "note"
    WARN = "warn"
    FATAL = "fatal"


@dataclass(frozen=True)
class Diagnostic:
    """A problem worth a human's attention, stated by whoever found it."""

    level: Level
    text: str
    where: Path | None = None
    remedy: str = ""

    def render(self) -> str:
        """The human-readable body, without a level marker — that is the channel's.

        The remedy gets its own line, indented under the notice's bullet: the
        banner prints `  • {text}` and indents no continuation of its own, so a
        producer that wants alignment does it here — the same way
        ``wrap_runner._broken_hook_refs_text`` already does.
        """
        body = f"{self.where}: {self.text}" if self.where is not None else self.text
        return f"{body}\n    {self.remedy}" if self.remedy else body


#: What each level is called on the plain-text channel. The banner has its own
#: markers and colours (``startup_notices._print_startup_notices``); this is the
#: spelling everywhere else, and it is the only one.
_STDERR_MARKER = {Level.NOTE: "NOTE", Level.WARN: "WARN", Level.FATAL: "Error"}


def emit_to_stderr(diagnostics: Sequence[Diagnostic]) -> None:
    """Write diagnostics to stderr — the fallback channel when nobody collects them."""
    for diag in diagnostics:
        print(f"{_STDERR_MARKER[diag.level]}: {diag.render()}", file=sys.stderr)
