"""ai-hats-cline — Cline surface plugin for ai-hats (HATS-956).

Registers `ClineSurface` under the `ai_hats.providers` entry-point group so the
`ai-hats` integrator discovers the `cline` CLI as a first-class provider with
zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:
    from .parser import ClineParser  # noqa: F401
    from .provider import ClineSurface  # noqa: F401

# Bound lazily (PEP 562): `hook_dispatcher` next door is a fresh process per tool
# call, and `provider` costs it the whole surface contract.
_HOMES = {"ClineParser": ".parser", "ClineSurface": ".provider"}

__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES)
