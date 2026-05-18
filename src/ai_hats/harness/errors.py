"""Harness reliability error types (HATS-378).

These errors carry diagnostic context for downstream meta-PROP routing.
A failure raised as a :class:`HarnessReliabilityError` (or subclass)
should be filed under ``target=harness-incident`` rather than a target
that blames the reporting role itself.
"""

from __future__ import annotations


class HarnessReliabilityError(RuntimeError):
    """Base class for harness-layer failures.

    Distinct from :class:`~ai_hats.retro.session_review_runner.SessionReviewError`
    so callers can route meta-PROPs to ``harness-incident`` vs
    ``session-reviewer``.
    """

    def __init__(self, session_id: str, diagnostic: str = "") -> None:
        self.session_id = session_id
        self.diagnostic = diagnostic
        super().__init__(self._format())

    def _format(self) -> str:
        return (
            f"{type(self).__name__}: sub-session={self.session_id}; "
            f"{self.diagnostic}".rstrip("; ")
        )


class HarnessZeroOutputError(HarnessReliabilityError):
    """Reporting sub-agent exited cleanly but emitted no observable output.

    Both ``tokens.output`` and ``tool_calls`` are zero in the finalized
    metrics — the run was a no-op that previously was accepted as
    successful. Surfaced so the harness can file a
    ``target=harness-incident`` meta-PROP instead of silently losing the
    signal.
    """


class HarnessTimeoutError(HarnessReliabilityError):
    """Sub-agent run timed out after exhausting its retry budget."""
