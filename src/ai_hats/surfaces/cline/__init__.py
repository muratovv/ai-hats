"""ai-hats-cline — Cline surface plugin for ai-hats (HATS-956).

Registers `ClineSurface` under the `ai_hats.surface_registry` entry-point group so the
`ai-hats` integrator discovers the `cline` CLI as a first-class provider with
zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).
"""

from __future__ import annotations

from .parser import ClineParser
from .provider import ClineSurface

__all__ = ["ClineParser", "ClineSurface"]
