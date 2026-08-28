"""Agy surface plugin for ai-hats."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import AgySurface

__all__ = ["AgySurface"]


# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
def __getattr__(name: str) -> object:
    if name != "AgySurface":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .provider import AgySurface

    globals()[name] = AgySurface
    return AgySurface


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
