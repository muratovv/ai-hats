"""Every surface's profile, gathered — the rows live next to their surfaces.

A row belongs beside the code it describes, so the person changing a surface
sees its names without leaving the package. This module is the collector: it
answers "which surfaces are there" and "which one is this", and nothing else.
"""

from __future__ import annotations

from .agy.profile import PROFILE as AGY
from .claude.profile import PROFILE as CLAUDE
from .cline.profile import PROFILE as CLINE
from .codex.profile import PROFILE as CODEX
from .hook_channel import SurfaceProfile
from .opencode.profile import PROFILE as OPENCODE

#: Every in-process channel, for a check that must hold across all of them.
ALL: tuple[SurfaceProfile, ...] = (AGY, CLAUDE, CLINE, CODEX, OPENCODE)


def by_label(label: str) -> SurfaceProfile | None:
    """The profile ``label`` names, or ``None`` when no surface answers to it."""
    return next((p for p in ALL if p.label == label), None)


__all__ = ["AGY", "ALL", "CLAUDE", "CLINE", "CODEX", "OPENCODE", "by_label"]
