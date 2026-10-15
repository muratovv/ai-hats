"""The initial-input injector: debounce, paste-then-Enter ordering, fail-open."""

from __future__ import annotations

from ai_hats.initial_input import InitialInputInjector


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _injector(clock: Clock, **overrides) -> InitialInputInjector:
    given = dict(text="fix the flaky test", quiet_s=0.4, cap_s=15.0, enter_delay_s=0.05)
    given.update(overrides)
    return InitialInputInjector(now=clock, **given)


def test_no_output_no_injection_until_cap_then_miss():
    clock = Clock()
    inj = _injector(clock)
    assert inj.pending
    assert inj.next_timeout() == 15.0  # only the cap is ticking

    clock.t = 16.0
    assert inj.on_tick() is None
    assert inj.missed
    assert not inj.pending


def test_paste_after_quiet_then_enter_on_the_next_tick():
    clock = Clock()
    inj = _injector(clock)

    clock.t = 1.0
    inj.on_output()  # boot output starts
    clock.t = 1.2
    inj.on_output()  # still redrawing — the quiet window restarts
    assert inj.on_tick() is None, "quiet has not elapsed yet"

    clock.t = 1.2 + 0.4
    paste = inj.on_tick()
    assert paste == b"\x1b[200~fix the flaky test\x1b[201~"
    assert inj.pending, "Enter is still owed"
    assert inj.next_timeout() == 0.05

    clock.t += 0.05
    assert inj.on_tick() == b"\r"
    assert not inj.pending
    assert not inj.missed


def test_output_during_the_window_defers_the_paste():
    clock = Clock()
    inj = _injector(clock)
    inj.on_output()
    clock.t = 0.39
    inj.on_output()  # a late redraw pushes the window out
    clock.t = 0.5
    assert inj.on_tick() is None
    clock.t = 0.39 + 0.4
    assert inj.on_tick() is not None


def test_cap_wins_over_an_unending_redraw():
    clock = Clock()
    inj = _injector(clock)
    inj.on_output()
    clock.t = 14.9
    inj.on_output()
    clock.t = 15.0
    assert inj.on_tick() is None
    assert inj.missed


def test_an_esc_in_the_prompt_cannot_forge_the_paste_close():
    clock = Clock()
    inj = _injector(clock, text="one\x1b[201~\rtwo")
    inj.on_output()
    clock.t = 0.4
    paste = inj.on_tick()
    # ESC is stripped; the remaining "[201~" is inert text without it.
    assert paste == b"\x1b[200~one[201~\rtwo\x1b[201~"
