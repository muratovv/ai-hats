"""Transitional shim — the task CLI moved to ``ai_hats_tracker.cli.task``
(HATS-934). Retires in HATS-935 slice 19 (direct repoint + delete).
"""

from ai_hats_tracker.cli.task import task

__all__ = ["task"]
