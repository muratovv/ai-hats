"""Unit coverage for the finalize-window SIGINT shield — HATS-1426.

The shield holds a stray Ctrl-C off the post-session finalize window (a single
press used to kill whichever step was running, silently). Handler installation,
the sub-threshold hold, and the escape trip are exercised here by calling the
installed handler directly; real signal delivery is proven in
``tests/test_sigint_shield_real_signal.py``.
"""

from __future__ import annotations

import io
import itertools
import logging
import signal
import threading

import pytest

from ai_hats import runtime_common
from ai_hats.runtime import FinalizeAborted, sigint_shield


def test_handler_installed_inside_and_restored_after():
    # GIVEN the SIGINT handler in force before the window
    previous = signal.getsignal(signal.SIGINT)

    # WHEN the shield is entered
    with sigint_shield(notice=io.StringIO()):
        inside = signal.getsignal(signal.SIGINT)

    # THEN it swapped in its own handler and put the original back on exit
    assert inside is not previous
    assert callable(inside)
    assert signal.getsignal(signal.SIGINT) is previous


def test_sub_threshold_presses_are_held_and_announced():
    # GIVEN a shielded window
    notice = io.StringIO()
    with sigint_shield(notice=notice) as _:
        handler = signal.getsignal(signal.SIGINT)
        # WHEN two Ctrl-C arrive (below the 3-press threshold)
        handler(signal.SIGINT, None)
        handler(signal.SIGINT, None)

    # THEN neither aborted, and each press told the operator what to do
    assert notice.getvalue().count("press Ctrl-C 3× within 1.5s to abort") == 2


def test_third_press_in_window_trips_the_hatch():
    # GIVEN a shielded window
    notice = io.StringIO()
    with sigint_shield(notice=notice):
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)
        handler(signal.SIGINT, None)
        # WHEN the third press lands inside the window
        with pytest.raises(FinalizeAborted):
            handler(signal.SIGINT, None)

    # THEN the operator sees the abort line
    assert "finalize aborted by operator (code 130)" in notice.getvalue()


def test_slow_drip_never_trips(monkeypatch):
    # GIVEN presses spaced wider than the 1.5s window (clamped, never exhausts:
    # pytest itself calls monotonic while this patch is in force)
    ticks = [0.0, 2.0, 4.0, 6.0]
    seen = itertools.count()
    monkeypatch.setattr(
        runtime_common.time,
        "monotonic",
        lambda: ticks[min(next(seen), len(ticks) - 1)],
    )

    with sigint_shield(notice=io.StringIO()):
        handler = signal.getsignal(signal.SIGINT)
        # WHEN four of them arrive — THEN none accumulates to a trip
        for _ in range(4):
            handler(signal.SIGINT, None)


def test_abort_is_invisible_to_the_per_phase_catches():
    # GIVEN the HATS-086 catch every finalize phase wraps itself in
    # THEN the abort is neither of those types — it must reach the runner
    assert not issubclass(FinalizeAborted, Exception)
    assert not issubclass(FinalizeAborted, KeyboardInterrupt)

    caught = None
    try:
        raise FinalizeAborted("trip")
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 — the phase-level shape
        caught = exc
    except FinalizeAborted:
        caught = "reached-runner"
    assert caught == "reached-runner"


def test_shield_is_fail_open_off_the_main_thread(caplog):
    # GIVEN a worker thread, where signal.signal() is illegal
    ran: list[bool] = []

    def body():
        with sigint_shield(notice=io.StringIO()):
            ran.append(True)

    worker = threading.Thread(target=body)
    with caplog.at_level(logging.WARNING):
        worker.start()
        worker.join(timeout=5)

    # THEN the guarded work still ran, and the degradation is on the record
    assert ran == [True]
    assert "SIGINT shield not installed" in caplog.text
