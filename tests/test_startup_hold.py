"""HATS-825: pre-launch startup-hold policy.

The wrapped CLI's full-screen TUI clobbers anything printed before the spawn,
so ``WrapRunner`` holds the start banner before launching ONLY when a fail-open
startup step warned (10s). A clean start does not hold (nothing to read), and a
non-tty never holds (the ``never block session start`` invariant). The policy
lives in a pure function so it is testable without sleeping or a real terminal.
"""

import pytest

from ai_hats.runtime_common import (
    STARTUP_WARN_HOLD_SECONDS,
    _countdown_hold,
    _startup_hold_seconds,
)


def test_clean_start_on_tty_does_not_hold():
    assert _startup_hold_seconds(False, is_tty=True, env={}) == 0.0


def test_warning_on_tty_holds():
    assert _startup_hold_seconds(True, is_tty=True, env={}) == STARTUP_WARN_HOLD_SECONDS


def test_non_tty_never_holds():
    # Headless / CI must not be delayed, warnings or not (fail-open invariant).
    assert _startup_hold_seconds(False, is_tty=False, env={}) == 0.0
    assert _startup_hold_seconds(True, is_tty=False, env={}) == 0.0


def test_env_override_wins_over_every_case():
    env = {"AI_HATS_STARTUP_HOLD": "0"}
    assert _startup_hold_seconds(True, is_tty=True, env=env) == 0.0
    assert _startup_hold_seconds(False, is_tty=True, env=env) == 0.0
    # Override applies even on a non-tty (lets a power user force a hold).
    assert _startup_hold_seconds(False, is_tty=False, env={"AI_HATS_STARTUP_HOLD": "3"}) == 3.0


def test_malformed_override_is_ignored():
    env = {"AI_HATS_STARTUP_HOLD": "soon"}
    assert _startup_hold_seconds(True, is_tty=True, env=env) == STARTUP_WARN_HOLD_SECONDS


def test_negative_override_clamped_to_zero():
    assert _startup_hold_seconds(True, is_tty=True, env={"AI_HATS_STARTUP_HOLD": "-5"}) == 0.0


# ----- HATS-847: the countdown is skippable (Enter ends the wait) -----


def test_countdown_skips_on_enter_at_frame_n():
    # poll_skip first returns True on the 3rd frame → loop stops there.
    frames: list[int] = []
    calls = {"n": 0}

    def poll(_timeout):
        calls["n"] += 1
        return calls["n"] == 3

    skipped = _countdown_hold(10, render=frames.append, poll_skip=poll)
    assert skipped is True
    assert frames == [10, 9, 8]  # rendered exactly until the skip, no further


def test_countdown_runs_full_when_never_skipped():
    frames: list[int] = []
    skipped = _countdown_hold(4, render=frames.append, poll_skip=lambda _t: False)
    assert skipped is False
    assert frames == [4, 3, 2, 1]


def test_countdown_zero_seconds_renders_nothing():
    frames: list[int] = []
    skipped = _countdown_hold(0, render=frames.append, poll_skip=lambda _t: True)
    assert skipped is False
    assert frames == []


def test_countdown_poll_gets_one_second_budget():
    # The per-frame wait is delegated to poll_skip with a 1 s budget so the
    # countdown ticks at 1 Hz (regression guard on the cadence contract).
    budgets: list[float] = []

    def poll(timeout):
        budgets.append(timeout)
        return False

    _countdown_hold(2, render=lambda _r: None, poll_skip=poll)
    assert budgets == [1.0, 1.0]


# ----- HATS-847: terminal glue (_poll_enter) -----


class _FakeStdin:
    def __init__(self, *, tty: bool, line: str = ""):
        self._tty = tty
        self._line = line
        self.read_calls = 0

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        self.read_calls += 1
        return self._line


def test_poll_enter_returns_true_and_drains_on_ready(monkeypatch):
    import ai_hats.wrap_runner as wr

    fake = _FakeStdin(tty=True, line="\n")
    monkeypatch.setattr(wr.sys, "stdin", fake)
    monkeypatch.setattr(wr.select, "select", lambda r, _w, _x, _t: (r, [], []))

    assert wr.WrapRunner._poll_enter(1.0) is True
    assert fake.read_calls == 1  # the Enter line was drained, not left for the TUI


def test_poll_enter_returns_false_on_timeout(monkeypatch):
    import ai_hats.wrap_runner as wr

    fake = _FakeStdin(tty=True)
    monkeypatch.setattr(wr.sys, "stdin", fake)
    monkeypatch.setattr(wr.select, "select", lambda _r, _w, _x, _t: ([], [], []))

    assert wr.WrapRunner._poll_enter(1.0) is False
    assert fake.read_calls == 0  # nothing typed → nothing drained


def test_poll_enter_non_tty_sleeps_and_never_skips(monkeypatch):
    import ai_hats.wrap_runner as wr

    fake = _FakeStdin(tty=False)
    slept: list[float] = []
    monkeypatch.setattr(wr.sys, "stdin", fake)
    monkeypatch.setattr(wr.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(
        wr.select,
        "select",
        lambda *_a, **_k: pytest.fail("select must not be called off a TTY"),
    )

    assert wr.WrapRunner._poll_enter(1.0) is False
    assert slept == [1.0]
