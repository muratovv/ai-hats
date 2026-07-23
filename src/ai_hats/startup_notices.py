"""Pre-launch startup notices and the read-hold (HATS-825 / HATS-833).

The wrapped CLI's full-screen TUI tears the terminal into the alternate screen
buffer the instant it spawns, clobbering anything ``run()`` printed before it —
including a fail-open startup warning. A brief hold when a startup step warned
gives the human a beat to read it before a session's work runs against a degraded
setup; a clean start holds for nothing. Extracted from ``runtime_common``
(HATS-970); that module keeps a back-compat re-export.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import NoReturn

STARTUP_WARN_HOLD_SECONDS = 10.0



def _startup_hold_seconds(
    has_warnings: bool,
    *,
    is_tty: bool,
    env: dict[str, str] | None = None,
) -> float:
    """Seconds to hold the start banner before launching the wrapped TUI.

    Policy: ``10s`` when a fail-open startup step emitted a warning, otherwise
    **no hold** — a clean start has nothing to surface, and a **non-tty**
    (headless/CI) run must not be delayed (the ``never block session start``
    fail-open invariant). ``AI_HATS_STARTUP_HOLD`` overrides the delay for
    every case (set ``0`` to disable, including in tests); a malformed value
    is ignored. Pure over its inputs so the policy is unit-testable without
    sleeping or a real terminal.
    """
    env = env if env is not None else os.environ
    override = env.get("AI_HATS_STARTUP_HOLD")
    if override is not None:
        try:
            return max(0.0, float(override))
        except ValueError:
            pass
    if not is_tty or not has_warnings:
        return 0.0
    return STARTUP_WARN_HOLD_SECONDS


def _countdown_hold(seconds, *, render, poll_skip) -> bool:
    """Run a 1 Hz countdown that the user can cut short (HATS-847).

    Pure loop, no I/O of its own — the caller injects both effects so the
    skip/complete behaviour is unit-testable without a real terminal or
    sleeping. For ``remaining`` from ``int(seconds)`` down to ``1``: draw the
    frame via ``render(remaining)``, then block up to one second in
    ``poll_skip(1.0)``. The moment ``poll_skip`` returns truthy (the user
    pressed Enter), stop early and return ``True`` (skipped); otherwise return
    ``False`` after the full count. ``poll_skip`` owns the per-frame wait, so it
    must block ~1 s when idle — that is what keeps the countdown ticking at 1 Hz.
    """
    for remaining in range(int(seconds), 0, -1):
        render(remaining)
        if poll_skip(1.0):
            return True
    return False


@dataclass(frozen=True)
class StartupNotice:
    """One pre-launch line surfaced during the startup hold (HATS-833).

    ``level``:
        ``"note"`` — informational success (e.g. a managed-hook heal). Rendered
            bold-green; means "we fixed drift", not "something is wrong".
        ``"warn"`` — a fail-open startup step degraded (resync raised, finalize
            preload failed, drift left unhealed under version-skew). Rendered
            bold-yellow.
    Both levels trigger the hold so the human can read them; a clean start emits
    neither and holds for nothing.
    """

    level: str
    text: str


def _print_startup_notices(notices: list[StartupNotice]) -> None:
    """Render startup notices before the hold: ✓ notes (green), ⚠ warns (yellow), ✕ fatals (red).
    Generalizes the warnings-only channel (HATS-825 → HATS-833).
    """
    notes = [n for n in notices if n.level == "note"]
    fatals = [n for n in notices if n.level in ("fatal", "error")]
    warns = [n for n in notices if n.level not in ("note", "fatal", "error")]
    g, y, r, rst = "\033[1;32m", "\033[1;33m", "\033[1;31m", "\033[0m"
    if notes:
        print(f"{g}✓ {len(notes)} startup note(s):{rst}")
        for n in notes:
            print(f"{g}  • {n.text}{rst}")
    if warns:
        print(f"{y}⚠ {len(warns)} startup warning(s):{rst}")
        for n in warns:
            print(f"{y}  • {n.text}{rst}")
    if fatals:
        for n in fatals:
            sys.stderr.write(f"{r}Error:{rst} {n.text}\n")


def _print_startup_warnings(warnings: list[str]) -> None:
    """Back-compat shim (HATS-833): render plain warning strings via the
    structured notice channel."""
    _print_startup_notices([StartupNotice("warn", w) for w in warnings])


def show_and_hold_startup_notices(notices, *, is_tty, sleep, env=None) -> None:
    """User-facing startup notices: notices present → render them and hold before
    launch so they're read; nothing to show → no render, no hold (HATS-833).

    Single owner of the "notices exist ⇒ show and wait" decision (the hold
    *policy* stays in :func:`_startup_hold_seconds`). ``sleep(delay)`` performs
    the actual wait — the caller injects a Ctrl-C-aware countdown so this stays
    free of PTY/TUI concerns and unit-testable.
    """
    delay = _startup_hold_seconds(bool(notices), is_tty=is_tty, env=env)
    if delay <= 0:
        return
    _print_startup_notices(notices)
    sleep(delay)


def show_fatal_notice_and_exit(text: str, *, exit_code: int = 1) -> NoReturn:
    """Render a fatal notice via the banner channel and terminate the session immediately."""
    _print_startup_notices([StartupNotice("fatal", text)])
    sys.exit(exit_code)

