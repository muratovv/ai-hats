"""OpenAI Codex CLI surface plugin for ai-hats (HATS-1531)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import CodexSurface

__all__ = ["CodexSurface"]


# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
def __getattr__(name: str) -> object:
    if name != "CodexSurface":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .provider import CodexSurface

    globals()[name] = CodexSurface
    return CodexSurface


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
