"""ai-hats-cline — Cline surface plugin for ai-hats (HATS-956).

Registers `ClineSurface` under the `ai_hats.providers` entry-point group so the
`ai-hats` integrator discovers the `cline` CLI as a first-class provider with
zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parser import ClineParser  # noqa: F401
    from .provider import ClineSurface  # noqa: F401

# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
_HOMES = {"ClineParser": ".parser", "ClineSurface": ".provider"}

__all__ = sorted(_HOMES)


def __getattr__(name: str) -> object:
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(home, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
