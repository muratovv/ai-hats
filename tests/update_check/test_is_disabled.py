"""Unit tests for the ``is_disabled`` opt-out helper."""

from __future__ import annotations

import pytest

from ai_hats.update_check import OPT_OUT_ENV, is_disabled


def test_is_disabled_false_when_unset(monkeypatch):
    monkeypatch.delenv(OPT_OUT_ENV, raising=False)
    assert is_disabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "anything"])
def test_is_disabled_true_when_set(monkeypatch, val):
    monkeypatch.setenv(OPT_OUT_ENV, val)
    assert is_disabled() is True


def test_is_disabled_false_when_empty(monkeypatch):
    monkeypatch.setenv(OPT_OUT_ENV, "")
    assert is_disabled() is False
