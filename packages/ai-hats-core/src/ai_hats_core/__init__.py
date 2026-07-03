"""Core primitives and shared mechanisms for the ai-hats framework.

Minimal dependencies, each load-bearing (HATS-862 F2: pydantic — the base of
the model layer). No domain schemas (ADR-0014 core contract).
"""

from ai_hats_core.atomic_io import atomic_write_bytes, atomic_write_text
from ai_hats_core.composition import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats_core.git_env import scrubbed_git_env
from ai_hats_core.yaml_model import YamlModel

__all__ = [
    "ComponentKind",
    "CompositionResult",
    "ResolvedComponent",
    "YamlModel",
    "atomic_write_bytes",
    "atomic_write_text",
    "scrubbed_git_env",
]
