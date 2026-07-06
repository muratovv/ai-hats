"""Transitional shim — the attach CLI moved to ``ai_hats_tracker.cli.attach``
(HATS-934). Retires in HATS-935 slice 19 (direct repoint + delete).
"""

from ai_hats_tracker.cli.attach import attach

__all__ = ["attach"]
