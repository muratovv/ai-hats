"""Named constants for builtin-library SOURCE resolution (HATS-831).

A dependency-free leaf within the ``paths`` package. Lifting these out of inline
magic strings makes them referenceable — by the resolver (``library``), the
consumers (assembler, pipeline loader/harness), and the single-home guard test
(``test_builtin_library_resolver_single_home``).
"""

from __future__ import annotations

# Installed package whose data dir IS the shipped builtin ``library/`` tree.
LIBRARY_PKG = "ai_hats.library"

# Composition layers under the library root, lowest priority first.
LIBRARY_LAYERS = ("core", "usage")

# Project-local (downstream) library dir name — the topology-B layer that an
# agent may edit inside a linked worktree (distinct from the builtin ``library/``).
LIBRARIES_DIRNAME = "libraries"

# Builtin hooks subdir under the library root.
HOOKS_DIRNAME = "hooks"

# Builtin core-pipeline YAML location under the library root.
PIPELINES_SUBPATH = ("core", "pipelines")

# Project config filename (moved from ai_hats.constants, HATS-917)
PROJECT_CONFIG = "ai-hats.yaml"

# Env override for the builtin-library root (validated both-core-and-usage).
ENV_LIBRARY_ROOT = "AI_HATS_LIBRARY_ROOT"

# Env-var names read inside the paths leaf (HATS-917)
ENV_AI_HATS_DIR = "AI_HATS_DIR"
ENV_AI_HATS_VENV = "AI_HATS_VENV"

__all__ = [
    "LIBRARY_PKG",
    "LIBRARY_LAYERS",
    "LIBRARIES_DIRNAME",
    "HOOKS_DIRNAME",
    "PIPELINES_SUBPATH",
    "PROJECT_CONFIG",
    "ENV_LIBRARY_ROOT",
    "ENV_AI_HATS_DIR",
    "ENV_AI_HATS_VENV",
]
