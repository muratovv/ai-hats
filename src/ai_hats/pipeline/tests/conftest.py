"""Fixtures for the ``pipeline`` area's own tests (ADR-0026 D5/D7).

This tree is a SIBLING of ``tests/``, so ``tests/conftest.py`` never reaches it
— the gap ``packages/*/tests`` has had since HATS-570, answered the same way:
what every tree in ``testpaths`` needs lives in the **repo-root**
``conftest.py`` (already covering this one), and what a single suite needs that
suite declares, as ``packages/ai-hats-rack/tests/conftest.py`` does.

Only the library-layer pin below has a reader here; the rest of the shared
suite's fixtures guard subsystems this area does not touch.
"""  # comment-length: allow — why this tree needs no copy of tests/conftest.py

from __future__ import annotations

import os

import pytest

# HATS-1429: dropped at import, not in the fixture — ``load_core_pipeline``
# resolves library layers at COLLECTION time. Both halves, never one: HATS-897
# scopes AI_HATS_DIR *by* the pin.
for _pinned in ("AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
    os.environ.pop(_pinned, None)


@pytest.fixture(autouse=True)
def _isolate_ai_hats_dir(monkeypatch):
    """Mirrors the main suite's HATS-671 fixture: an ambient ``AI_HATS_DIR``
    outranks the caller's project dir, so a developer running inside a live
    session would read that session's library layers, not the shipped ones.
    """
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)  # HATS-897 pair var
    yield
