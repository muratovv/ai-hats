"""Initial-prompt injection into a surface TUI over the PTY master.

A surface whose CLI has no positional prompt (kimi) gets the operator's
first-turn prompt typed into its TUI instead: wait until the boot redraw goes
quiet, write the bracketed-paste-wrapped text, then `\r` on the next tick —
the deferred-Enter precedent of `pty_relay.FdPtyTap._handle_raw_input`, which
keeps the TUI from racing the paste and the submit in one write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

_PASTE_OPEN = b"\x1b[200~"
_PASTE_CLOSE = b"\x1b[201~"

#: State constants of the injector's tiny machine.
_WAIT_OUTPUT = "wait_output"  # nothing drawn yet — debounce starts at first output
_ARMED = "armed"  # output seen; waiting for `quiet_s` of silence
_PASTED = "pasted"  # paste blob written; `\r` goes out on the next tick
_DONE = "done"
_MISSED = "missed"  # cap expired before injection — fail open


@dataclass
class InitialInputInjector:
    """Feed PTY-master activity in, get bytes to write back out.

    Pure timing logic — the select loop owns the fds. The clock is a seam.
    """

    text: str
    quiet_s: float = 0.4
    cap_s: float = 15.0
    enter_delay_s: float = 0.05
    now: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._started = self.now()
        self._last_output: float | None = None
        self._state = _WAIT_OUTPUT
        # An ESC in the body could forge the paste-close sequence and submit
        # early — strip it; shell argv carrying ESC is pathological anyway.
        self._payload = _PASTE_OPEN + self.text.replace("\x1b", "").encode("utf-8") + _PASTE_CLOSE

    @property
    def pending(self) -> bool:
        """Whether the loop must keep a select timeout for this injector."""
        return self._state in (_WAIT_OUTPUT, _ARMED, _PASTED)

    @property
    def missed(self) -> bool:
        return self._state == _MISSED

    def on_output(self) -> None:
        """The TUI drew something — (re)start the quiet window."""
        self._last_output = self.now()
        if self._state == _WAIT_OUTPUT:
            self._state = _ARMED

    def next_timeout(self) -> float | None:
        """How long select may block before the injector needs a tick."""
        if not self.pending:
            return None
        now = self.now()
        cap_left = max(0.0, self._started + self.cap_s - now)
        if self._state == _PASTED:
            return self.enter_delay_s
        if self._state == _ARMED and self._last_output is not None:
            quiet_left = max(0.0, self._last_output + self.quiet_s - now)
            return min(quiet_left, cap_left)
        return cap_left

    def on_tick(self) -> bytes | None:
        """A select timeout elapsed — emit the next write, if any."""
        now = self.now()
        if self._state == _PASTED:
            self._state = _DONE
            return b"\r"
        if now - self._started >= self.cap_s:
            self._state = _MISSED
            return None
        if (
            self._state == _ARMED
            and self._last_output is not None
            and now - self._last_output >= self.quiet_s
        ):
            self._state = _PASTED
            return self._payload
        return None
