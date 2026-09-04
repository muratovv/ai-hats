"""Named constants for builtin-library SOURCE resolution (HATS-831).

A dependency-free leaf within the ``paths`` package. Lifting these out of inline
magic strings makes them referenceable — by the resolver (``library``), the
consumers (assembler, pipeline loader/harness), and the single-home guard test
(``test_builtin_library_resolver_single_home``).
"""

from __future__ import annotations

# Installed package whose data dir IS the shipped builtin library tree —
# the standalone ai-hats-library data package (HATS-876/T18; was ai_hats.library).
LIBRARY_PKG = "ai_hats_library"

# Composition layers under the library root, lowest priority first.
LIBRARY_LAYERS = ("core", "usage", "ai-hats-dev")

# The subset a dir must hold to BE a library root (HATS-1834). Kept apart from
# LIBRARY_LAYERS so a newer integrator still validates an older library wheel
# that predates a layer — `is_library_root` is an all() over this tuple.
REQUIRED_LIBRARY_LAYERS = ("core", "usage")

# Project-local (downstream) library dir name — the topology-B layer that an
# agent may edit inside a linked worktree (distinct from the builtin ``library/``).
LIBRARIES_DIRNAME = "libraries"

# Builtin hooks subdir under the library root.
HOOKS_DIRNAME = "hooks"

# Builtin core-pipeline YAML location under the library root.
PIPELINES_SUBPATH = ("core", "pipelines")

# Project config filename (moved from ai_hats.constants, HATS-917)
PROJECT_CONFIG = "ai-hats.yaml"

# HATS-1613: re-exported from the env leaf, not re-declared — one spelling, one
# home (ADR-0025 D1). Kept importable from here so existing callers are unchanged.
from ..env import (  # noqa: E402
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    ENV_AI_HATS_USER_HOME,
    ENV_AI_HATS_VENV,
    ENV_LIBRARY_ROOT,
)

# HATS-792: highest ai-hats.yaml ``schema_version`` this binary understands. A
# higher value was written by a NEWER ai-hats whose format we cannot read; every
# reader — the full pydantic one and the bootstrap raw readers alike — refuses
# it. Lives in the leaf so both can import it without a config dependency.
KNOWN_SCHEMA_VERSION = 4

__all__ = [
    "KNOWN_SCHEMA_VERSION",
    "LIBRARY_PKG",
    "LIBRARY_LAYERS",
    "REQUIRED_LIBRARY_LAYERS",
    "LIBRARIES_DIRNAME",
    "HOOKS_DIRNAME",
    "PIPELINES_SUBPATH",
    "PROJECT_CONFIG",
    "ENV_LIBRARY_ROOT",
    "ENV_AI_HATS_USER_HOME",
    "ENV_AI_HATS_DIR",
    "ENV_AI_HATS_VENV",
    "AI_HATS_PROJECT_DIR_ENV",
]
