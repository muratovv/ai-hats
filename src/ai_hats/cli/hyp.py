"""Transitional shim — the hyp CLI moved to ``ai_hats_tracker.cli.hyp``
(HATS-935). Retires in HATS-935 slice 19 (direct repoint + delete).
"""

from ai_hats_tracker.cli.hyp import hyp

__all__ = ["hyp"]
