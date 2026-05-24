"""Shared pytest fixtures for the ai-hats test suite.

HATS-470: :mod:`ai_hats.safe_delete` keeps a per-process trash session
in module-level state. Without an autouse reset, the first test to
trigger a destructive op pins the session for every subsequent test,
which corrupts assertions about default vs custom trash base, manifest
content, and the under-trash recursion guard.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_safe_delete_session(monkeypatch):
    """Reset trash-bin module state + clear AI_HATS_TRASH_DIR per test.

    Runs for EVERY test (autouse) to guarantee that ``safe_delete``
    behaves as if it just loaded. No yields/teardowns needed beyond the
    final reset because module state is process-local and tests don't
    fork.
    """
    from ai_hats import safe_delete

    safe_delete.reset_session()
    monkeypatch.delenv(safe_delete.ENV_TRASH_DIR, raising=False)
    yield
    safe_delete.reset_session()
