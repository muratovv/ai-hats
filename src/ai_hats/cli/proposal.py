"""Transitional shim — the proposal CLI moved to ``ai_hats_tracker.cli.proposal``
(HATS-935). Retires in HATS-935 slice 19 (direct repoint + delete).
"""

from ai_hats_tracker.cli.proposal import proposal

__all__ = ["proposal"]
