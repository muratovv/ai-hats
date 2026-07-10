"""ai-hats-library — the framework's agent library as a data-only package.

This is a **thin shim**: it holds no framework runtime code. It exists so that
``importlib.resources.files("ai_hats_library")`` resolves the shipped content —
the ``core/`` and ``usage/`` layers plus ``hooks/`` — uniformly across editable
and wheel installs. The integrator (``ai-hats``) reads that path through a single
``as_file`` seam (``ai_hats.paths.library``); any other consumer can do the same.

Imports the standard library only (guarded by ``tests/test_library_boundary.py``).
"""

from __future__ import annotations

# Library format-schema version; mirrored as data in manifest.yaml (HATS-876).
LIBRARY_SCHEMA_VERSION = 1

__all__ = ["LIBRARY_SCHEMA_VERSION"]
