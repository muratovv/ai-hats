"""Standalone-suite env defaults for the ``ai-hats-wt`` package tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _legacy_ack_flags_are_absent(monkeypatch: pytest.MonkeyPatch):
    """The standalone engine must not need session authorization flags."""
    monkeypatch.delenv("AI_HATS_MERGE_ACK", raising=False)
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
