"""Consent gate: a person issues, two readers check, neither ever asks (ADR-0029).

``check`` and ``issue`` know nothing about ai-hats — that knowledge lives in
``host``, the one module that stays behind when the engine ships on its own
(HATS-1740).
"""

from .check import (
    GRANT_VERSION,
    JOURNAL_FILENAME,
    Operation,
    Outcome,
    Verdict,
    check,
    file_journal,
    grants_dir,
    journal_entry,
    live_grants,
    mode_refusal,
    record,
    store_root_from,
)
from .issue import DEFAULT_WINDOW_MINUTES, Grant, IssueError, Radius, issue

__all__ = [
    "DEFAULT_WINDOW_MINUTES",
    "GRANT_VERSION",
    "JOURNAL_FILENAME",
    "Grant",
    "IssueError",
    "Operation",
    "Outcome",
    "Radius",
    "Verdict",
    "check",
    "file_journal",
    "grants_dir",
    "issue",
    "journal_entry",
    "live_grants",
    "mode_refusal",
    "record",
    "store_root_from",
]
