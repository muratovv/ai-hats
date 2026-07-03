"""Dependency-free core primitives for the ai-hats framework — pure stdlib, no deps."""

from ai_hats_core.atomic_io import atomic_write_bytes, atomic_write_text
from ai_hats_core.composition import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats_core.git_env import scrubbed_git_env

__all__ = [
    "ComponentKind",
    "CompositionResult",
    "ResolvedComponent",
    "atomic_write_bytes",
    "atomic_write_text",
    "scrubbed_git_env",
]
