"""OpenCode surface plugin for ai-hats (HATS-1788)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import OpenCodeSurface

__all__ = ["OpenCodeSurface"]


# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
def __getattr__(name: str) -> object:
    if name != "OpenCodeSurface":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .provider import OpenCodeSurface

    globals()[name] = OpenCodeSurface
    return OpenCodeSurface


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
