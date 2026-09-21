"""Re-export of ``ai_hats_core.diagnostics`` — the type moved to core so the recovery contract can name it."""

from __future__ import annotations

from ai_hats_core.diagnostics import Diagnostic, Level, emit_to_stderr

__all__ = ["Level", "Diagnostic", "emit_to_stderr"]
