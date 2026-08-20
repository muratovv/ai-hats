"""Pre-launch startup notices and the read-hold (HATS-825 / HATS-833).

The wrapped CLI's full-screen TUI tears the terminal into the alternate screen
buffer the instant it spawns, clobbering anything ``run()`` printed before it —
including a fail-open startup warning. A brief hold when a startup step warned
gives the human a beat to read it before a session's work runs against a degraded
setup; a clean start holds for nothing. Extracted from ``runtime_common``
(HATS-970); that module keeps a back-compat re-export.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

logger = logging.getLogger(__name__)

_ANSI_REGEX = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\r")


def strip_ansi_and_control_codes(text: str) -> str:
    """Remove ANSI escape sequences and control characters from banner text."""
    if not text:
        return ""
    return _ANSI_REGEX.sub("", text)


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
    fail-open invariant). ``AI_HATS_NON_INTERACTIVE`` overrides everything to ``0.0``.
    ``AI_HATS_STARTUP_HOLD`` overrides the delay (set ``0`` to disable, including in tests);
    a malformed value is ignored. Pure over its inputs so the policy is unit-testable without
    sleeping or a real terminal.
    """
    env = env if env is not None else os.environ
    non_interactive = env.get("AI_HATS_NON_INTERACTIVE", "").strip().lower()
    if non_interactive in ("1", "true", "yes", "on"):
        return 0.0
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
        ``"fatal"`` (``"error"``) — the launch does not proceed. Rendered red on
            stderr by ``_print_startup_notices`` and normally reached through
            :func:`show_fatal_notice_and_exit`, which also records it. Listed
            here since HATS-1581: it was already handled and already used at
            three call sites while this docstring still named only two levels,
            so a reader concluded the channel could not refuse.
    ``note`` and ``warn`` trigger the hold so the human can read them; a clean
    start emits neither and holds for nothing.
    """  # comment-length: allow — the omitted level cost a wrong plan once

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

    Rendering does not depend on the hold (HATS-1753). It used to: a zero delay
    returned before the print, so a headless, CI or ``AI_HATS_NON_INTERACTIVE``
    launch wrote every notice to ``diagnostics.json`` and showed none of them.
    "Never delayed" was the invariant; "never shown" was the accident.
    """  # comment-length: allow — the render/hold split is the fix, and it reads as a no-op
    if not notices:
        return
    _print_startup_notices(notices)
    delay = _startup_hold_seconds(True, is_tty=is_tty, env=env)
    if delay > 0:
        sleep(delay)


def show_fatal_notice_and_exit(
    text: str,
    *,
    exit_code: int = 1,
    session_dir: Path | str | None = None,
) -> NoReturn:
    """Render a fatal notice via the banner channel and terminate the session immediately."""
    clean_text = strip_ansi_and_control_codes(text)
    if session_dir is not None:
        save_session_diagnostics(
            session_dir,
            "startup",
            {
                "hold_seconds": 0.0,
                "notices": [{"level": "fatal", "text": clean_text}],
            },
        )
    _print_startup_notices([StartupNotice("fatal", text)])
    sys.exit(exit_code)


def save_session_diagnostics(
    session_dir: Path | str | None,
    key: str,
    data: dict,
) -> None:
    """Atomic read-modify-write persistence for service-channel diagnostics into `<session_dir>/diagnostics.json`.

    HATS-1221: Captures both pre-session notices and post-session banners into
    top-level keys (`startup`, `completion`, `retro_reminder`, `update_banner`).

    Fail-soft (HATS-086): catches (Exception, KeyboardInterrupt) so a diagnostic write failure
    or SIGINT never crashes session setup or teardown. Uses in-dir atomic temporary files to
    avoid cross-device link errors (`EXDEV`).
    """
    if session_dir is None:
        return
    try:
        s_dir = Path(session_dir)
        if not s_dir.exists() or not s_dir.is_dir():
            return

        diag_file = s_dir / "diagnostics.json"
        existing: dict = {}
        if diag_file.is_file():
            try:
                raw = json.loads(diag_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
                else:
                    try:
                        diag_file.rename(s_dir / "diagnostics.json.corrupted")
                    except Exception:  # noqa: S110
                        # silent-ok: the quarantine rename is itself the recovery
                        pass
            except Exception:
                try:
                    diag_file.rename(s_dir / "diagnostics.json.corrupted")
                except Exception:  # noqa: S110
                    # silent-ok: the quarantine rename is itself the recovery
                    pass

        existing["schema_version"] = 1
        existing[key] = data

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            dir=s_dir,
            prefix="diag_",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(existing, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, diag_file)
        except BaseException:
            try:
                tmp.close()
                os.unlink(tmp.name)  # safe-delete: ok tmp-file
            except Exception:  # silent-ok: temp cleanup before the bare raise below  # noqa: S110
                pass
            raise
    except (Exception, KeyboardInterrupt) as exc:
        logger.warning("save_session_diagnostics failed: %s", exc)
