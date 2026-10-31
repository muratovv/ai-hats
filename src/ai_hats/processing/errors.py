"""The one refusal type of processing: a plan is not produced (ADR-0035 D8)."""

from __future__ import annotations


class ProcessingError(RuntimeError):
    """Processing refused to produce a plan; the message names what and where."""
