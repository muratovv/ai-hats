"""The pipelines this product runs — the application's catalog, not the area's.

The pipeline area runs whatever config it is handed; knowing that `execute` or
`reflect-session` exist is knowledge about how ai-hats uses pipelines, so it lives
outside (ADR-0026 D14). Entries appear here as their callers migrate off
``pipeline.keys``; the module travels with the built-in steps when those leave.
"""

from __future__ import annotations

from .pipeline import PipelineConfig

EXECUTE = PipelineConfig(name="execute")
