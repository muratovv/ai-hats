"""Grok surface — xAI's `grok` CLI as an ai-hats harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:
    from .provider import GrokSurface  # noqa: F401

_HOMES = {
    "GrokSurface": ".provider",
}

__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES)
