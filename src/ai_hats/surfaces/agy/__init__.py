"""Agy surface plugin for ai-hats."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import AgySurface

__all__ = ["AgySurface"]


# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
def __getattr__(name: str) -> object:
    from importlib import import_module

    if name == "AgySurface":
        value = getattr(import_module(".provider", __name__), name)
        globals()[name] = value  # bound once; later lookups skip __getattr__
        return value
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Eager binding used to expose the submodules this facade imported; keep it.
    try:
        return import_module(f"{__name__}.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
