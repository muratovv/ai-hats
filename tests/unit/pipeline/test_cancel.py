"""Tests for the CancelToken cooperative cancellation primitive."""

from __future__ import annotations

from ai_hats.pipeline import CancelReason, CancelToken


def test_token_starts_uncancelled() -> None:
    token = CancelToken()
    assert token.cancelled is False
    assert token.reason is None


def test_cancel_sets_flag_and_reason() -> None:
    token = CancelToken()
    token.cancel(CancelReason.TIMEOUT)
    assert token.cancelled is True
    assert token.reason is CancelReason.TIMEOUT


def test_cancel_is_idempotent_first_reason_wins() -> None:
    token = CancelToken()
    token.cancel(CancelReason.TIMEOUT)
    token.cancel(CancelReason.EXTERNAL)
    assert token.reason is CancelReason.TIMEOUT
