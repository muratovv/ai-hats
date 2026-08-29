"""Claude Code surface plugin for ai-hats."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:
    from .provider import ClaudeSurface  # noqa: F401

_HOMES = {
    "ClaudeSurface": ".provider",
}

__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES)
