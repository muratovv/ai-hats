"""OpenCode surface plugin for ai-hats (HATS-1788)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:
    from .provider import OpenCodeSurface  # noqa: F401

_HOMES = {
    "OpenCodeSurface": ".provider",
}

__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES)
