"""Standalone-suite env defaults for the ``ai-hats-wt`` package tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _grant_merge_consent(monkeypatch: pytest.MonkeyPatch):
    """AI_HATS_MERGE_ACK=1 for every test (HATS-1019) — this suite tests
    lifecycle semantics; the consent contract has its own explicit test."""
    monkeypatch.setenv("AI_HATS_MERGE_ACK", "1")
    monkeypatch.setenv("AI_HATS_PLAN_ACK", "1")
