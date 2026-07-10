"""Scaffold sanity for the ai-hats-library data package (HATS-876 / T18)."""

from __future__ import annotations


def test_package_imports_and_exposes_schema_version() -> None:
    import ai_hats_library

    assert isinstance(ai_hats_library.LIBRARY_SCHEMA_VERSION, int)
    assert ai_hats_library.LIBRARY_SCHEMA_VERSION >= 1
