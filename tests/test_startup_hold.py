"""HATS-825: pre-launch startup-hold policy.

The wrapped CLI's full-screen TUI clobbers anything printed before the spawn,
so ``WrapRunner`` holds the start banner briefly before launching: 1s on a
clean start, 10s when a fail-open startup step warned, and not at all on a
non-tty (the ``never block session start`` invariant). The policy lives in a
pure function so it is testable without sleeping or a real terminal.
"""

from ai_hats.runtime_common import (
    STARTUP_HOLD_SECONDS,
    STARTUP_WARN_HOLD_SECONDS,
    _startup_hold_seconds,
)


def test_clean_start_on_tty_holds_default():
    assert _startup_hold_seconds(False, is_tty=True, env={}) == STARTUP_HOLD_SECONDS


def test_warning_on_tty_holds_longer():
    assert _startup_hold_seconds(True, is_tty=True, env={}) == STARTUP_WARN_HOLD_SECONDS
    assert STARTUP_WARN_HOLD_SECONDS > STARTUP_HOLD_SECONDS


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
