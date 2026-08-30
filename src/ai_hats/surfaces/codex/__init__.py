"""OpenAI Codex CLI surface plugin for ai-hats (HATS-1531)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:
    from .provider import CodexSurface  # noqa: F401

_HOMES = {
    "CodexSurface": ".provider",
}

__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES)
