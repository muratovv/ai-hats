"""Dependency-free core primitives for the ai-hats framework — pure stdlib, no deps."""

from ai_hats_core.atomic_io import atomic_write_bytes, atomic_write_text

__all__ = ["atomic_write_bytes", "atomic_write_text"]
