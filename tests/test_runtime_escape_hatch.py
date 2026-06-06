"""Unit coverage for the parent escape-hatch counter — HATS-679.

The escalation logic (``_scan_escape``) is a pure function so the per-byte
counting, sliding-window expiry, and "consecutive" reset rules can be verified
without spinning up a PTY. The single real-PTY revert-detection guard lives in
``tests/e2e/test_pty_escape_hatch.py``.
"""

from __future__ import annotations

from collections import deque

import pytest

from ai_hats.runtime import _ESCAPE_COUNT, _ESCAPE_WINDOW_S, _scan_escape

C = b"\x03"  # Ctrl-C


def test_default_constants_match_plan():
    # GIVEN the agreed escape gesture (HATS-679 plan)
    # THEN the module constants are the triple-Ctrl-C / 1.5s window contract
    assert _ESCAPE_COUNT == 3
    assert _ESCAPE_WINDOW_S == 1.5


def test_single_ctrl_c_is_dormant():
    # GIVEN a fresh streak
    presses: deque[float] = deque()
    # WHEN one Ctrl-C arrives
    forward, triggered = _scan_escape(C, presses, 0.0)
    # THEN it is forwarded untouched and does not fire (R2 dormancy)
    assert forward == C
    assert triggered is False


def test_double_ctrl_c_is_dormant():
    presses: deque[float] = deque()
    forward, triggered = _scan_escape(C + C, presses, 0.0)
    assert forward == C + C
    assert triggered is False


def test_normal_typing_never_fires():
    presses: deque[float] = deque()
    forward, triggered = _scan_escape(b"hello\r", presses, 0.0)
    assert forward == b"hello\r"
    assert triggered is False
    assert not presses  # no Ctrl-C banked


def test_batched_triple_fires_and_withholds_third():
    # GIVEN three Ctrl-C in a single read (the timing-independent path)
    presses: deque[float] = deque()
    # WHEN scanned at one instant
    forward, triggered = _scan_escape(C + C + C, presses, 10.0)
    # THEN it fires; the first two are forwarded, the 3rd is withheld
    assert triggered is True
    assert forward == C + C


def test_trailing_bytes_after_trigger_are_withheld():
    presses: deque[float] = deque()
    forward, triggered = _scan_escape(C + C + C + b"tail", presses, 0.0)
    assert triggered is True
    assert forward == C + C  # everything from the triggering byte on is dropped


def test_separate_reads_accumulate_then_fire():
    # GIVEN three Ctrl-C delivered as separate reads within the window
    presses: deque[float] = deque()
    f1, t1 = _scan_escape(C, presses, 0.0)
    f2, t2 = _scan_escape(C, presses, 0.1)
    f3, t3 = _scan_escape(C, presses, 0.2)
    # THEN the first two forward and don't fire; the third fires, forwards ""
    assert (f1, t1) == (C, False)
    assert (f2, t2) == (C, False)
    assert t3 is True
    assert f3 == b""


def test_non_ctrl_c_byte_resets_streak():
    # GIVEN two Ctrl-C then a normal byte then a Ctrl-C, all in one chunk
    presses: deque[float] = deque()
    forward, triggered = _scan_escape(C + C + b"x" + C, presses, 0.0)
    # THEN the 'x' clears the streak so the trailing Ctrl-C is only #1 → no fire
    assert triggered is False
    assert forward == C + C + b"x" + C
    assert len(presses) == 1


def test_interleaved_reset_across_reads():
    presses: deque[float] = deque()
    _scan_escape(C, presses, 0.0)
    _scan_escape(C, presses, 0.1)
    _scan_escape(b"x", presses, 0.2)  # reset
    assert not presses
    # only two Ctrl-C after the reset → still dormant
    _scan_escape(C, presses, 0.3)
    _, triggered = _scan_escape(C, presses, 0.4)
    assert triggered is False


def test_window_expiry_prevents_slow_drip():
    # GIVEN Ctrl-C spaced so no 3 fall inside a 1.5s sliding window
    presses: deque[float] = deque()
    _, t1 = _scan_escape(C, presses, 0.0)
    _, t2 = _scan_escape(C, presses, 1.0)
    _, t3 = _scan_escape(C, presses, 2.0)  # 2.0-0.0 > 1.5 → oldest dropped
    # THEN a slow drip never accumulates to a trigger
    assert (t1, t2, t3) == (False, False, False)
    assert len(presses) == 2  # only the 1.0 and 2.0 presses remain in-window


def test_trigger_clears_state_so_next_streak_starts_fresh():
    presses: deque[float] = deque()
    _, triggered = _scan_escape(C + C + C, presses, 0.0)
    assert triggered is True
    # after a fire the deque is emptied — a subsequent single press is dormant
    assert not presses
    _, again = _scan_escape(C, presses, 0.01)
    assert again is False


def test_custom_count_and_window_kwargs_honoured():
    presses: deque[float] = deque()
    _, triggered = _scan_escape(C + C, presses, 0.0, count=2)
    assert triggered is True


@pytest.mark.parametrize("count", [3, 4, 5])
def test_fires_exactly_at_threshold(count):
    presses: deque[float] = deque()
    # count-1 presses stay dormant
    below, t_below = _scan_escape(C * (count - 1), presses, 0.0, count=count)
    assert t_below is False
    assert below == C * (count - 1)
    # the next press (in-window) trips it
    _, t_at = _scan_escape(C, presses, 0.01, count=count)
    assert t_at is True
