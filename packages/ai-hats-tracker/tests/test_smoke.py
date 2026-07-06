"""Slice-1 scaffold smoke test — the empty package imports.

Replaced by ``test_tracker_standalone.py`` + ``test_boundary.py`` at the 0.1.0
capstone (slice 9); it exists only so the package is non-empty and discoverable
while the modules are lifted in.
"""

from __future__ import annotations


def test_import_ai_hats_tracker():
    import ai_hats_tracker

    assert ai_hats_tracker.__all__ == []
