"""ai-hats-cline — Cline surface plugin for ai-hats (HATS-956).

Registers `ClineProvider` under the `ai_hats.providers` entry-point group so the
`ai-hats` integrator discovers the `cline` CLI as a first-class provider with
zero edits to `src/ai_hats/**` (the T10 IoC seam, HATS-870).
"""

from __future__ import annotations

from ai_hats_cline.parser import ClineParser
from ai_hats_cline.provider import ClineProvider

__all__ = ["ClineParser", "ClineProvider"]
