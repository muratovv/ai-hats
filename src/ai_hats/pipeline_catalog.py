"""The pipelines this product runs — the application's catalog, not the area's.

The pipeline area runs whatever config it is handed; knowing that `execute` or
`reflect-session` exist is knowledge about how ai-hats uses pipelines, so it lives
outside (ADR-0026 D14). ``tests/test_area_boundary.py`` holds this list against the
shipped YAML files, so a new pipeline is declared here or it is not shipped. The
module travels with the built-in steps when those leave.
"""

from __future__ import annotations

from .pipeline import PipelineConfig

# TODO(HATS-1784): triage this list — finalize-* run inside a session rather than as
# pipelines, preview ships as YAML nothing loads, and the five reflect-* differ by a
# role and a prompt. Out of the HATS-1586 epic by supervisor's call.
EXECUTE = PipelineConfig(name="execute")
HUMAN = PipelineConfig(name="human")
INIT = PipelineConfig(name="init")
PREVIEW = PipelineConfig(name="preview")
FINALIZE_HITL = PipelineConfig(name="finalize-hitl")
FINALIZE_SUBAGENT = PipelineConfig(name="finalize-subagent")
REFLECT_SESSION = PipelineConfig(name="reflect-session")
REFLECT_ALL = PipelineConfig(name="reflect-all")
REFLECT_HYPOTHESIS_PHASE1 = PipelineConfig(name="reflect-hypothesis-phase1")
REFLECT_HYPOTHESIS_PHASE2 = PipelineConfig(name="reflect-hypothesis-phase2")
REFLECT_ROLE = PipelineConfig(name="reflect-role")
REFLECT_ISSUE = PipelineConfig(name="reflect-issue")

ALL: tuple[PipelineConfig, ...] = (
    EXECUTE,
    HUMAN,
    INIT,
    PREVIEW,
    FINALIZE_HITL,
    FINALIZE_SUBAGENT,
    REFLECT_SESSION,
    REFLECT_ALL,
    REFLECT_HYPOTHESIS_PHASE1,
    REFLECT_HYPOTHESIS_PHASE2,
    REFLECT_ROLE,
    REFLECT_ISSUE,
)
