"""Runtime helpers shared by wrap_runner (HITL) and subagent_runner (Automate):
hooks execution, session finalize/print, escape-hatch + PTY-reset, and
session-cache cleanup. Extracted from runtime.py (HATS-715)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

# HATS-649: the session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .constants import TraceTag
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .paths import (
    REASONING_LOG,
    TRANSCRIPT_TXT,
    claude_transcript_path,
    claude_transcripts_dir,
)
from .pipeline.keys import (
    KEY_CLAUDE_SESSION_ID,
    KEY_ERRORS,
    KEY_EXIT_CODE,
    KEY_PROJECT_DIR,
    KEY_SESSION_DIR,
    KEY_SESSION_ID,
    PIPELINE_FINALIZE_HITL,
    PIPELINE_FINALIZE_SUBAGENT,
)

if TYPE_CHECKING:
    from .observe import Session, SidecarTracer

logger = logging.getLogger(__name__)

# Sub-agent subprocess wall-clock limit. Exceeding this raises TimeoutExpired,
# which is handled by graceful finalize (partial transcript + exit_code=124).
SUBAGENT_SUBPROCESS_TIMEOUT_S = 600

# Exit code conventions for early termination. 124 matches GNU coreutils `timeout`.
SUBAGENT_EXIT_TIMEOUT = 124
SUBAGENT_EXIT_ERROR = 1

# HATS-215 / HATS-220: emitted on stdout before each PTY child spawn to
# neutralise terminal state a prior TUI session may have leaked (idempotent on a
# clean terminal). HATS-220: a leaked modifyOtherKeys=2 re-encoded plain Enter —
# see the ticket. Each sequence:
#   \x1b[=0;1u    — kitty-keyboard ABSOLUTE set (flags=0, mode=1); replaces the
#                   unreliable relative `\x1b[<u` pop.
#   \x1b[>4;0m    — modifyOtherKeys=0 (the HATS-220 leaker); keeps Enter→\r.
#   \x1b[20l      — LNM off (DEC ANSI mode 20); defensive.
#   \x1b>         — DECKPNM; keypad Enter sends \r, not \x1bOM.
#   \x1b[?2004l   — disable bracketed paste.
#   \x1b[?1l      — exit application cursor mode (DECCKM off).
#   \x1b[?25h     — show cursor (in case a crashed TUI hid it).
_TERM_RESET_PRELUDE = "\x1b[=0;1u\x1b[>4;0m\x1b[20l\x1b>\x1b[?2004l\x1b[?1l\x1b[?25h"


# HATS-679: parent escape-hatch for a wedged PTY provider. When the child
# (claude) wedges un-exitably — ignores Ctrl-C, never EOFs — the raw-mode
# passthrough in ``_pty_spawn`` forwards Ctrl-C as a byte, so the parent has no
# exit and the user is trapped. We can't reliably tell wedged from healthy-busy
# (every cheap liveness signal is ambiguous), so we use a behavioural probe:
# 3 Ctrl-Cs in a short window force the exit. A healthy claude EOFs on the 2nd,
# so the 3rd never lands — the hatch is dormant unless the child fails to honour
# the standard double-Ctrl-C quit.
_ESCAPE_CTRL_C = 0x03  # the Ctrl-C byte (VINTR) forwarded in raw mode
_ESCAPE_COUNT = 3  # consecutive Ctrl-C presses that trip the hatch
_ESCAPE_WINDOW_S = 1.5  # they must fall within this sliding window
_ESCAPE_NOTICE = b"\r\n[ai-hats] provider not responding to Ctrl-C; forcing exit (code 130).\r\n"


def _scan_escape(
    chunk: bytes,
    presses: deque[float],
    now: float,
    *,
    count: int = _ESCAPE_COUNT,
    window_s: float = _ESCAPE_WINDOW_S,
) -> tuple[bytes, bool]:
    """Scan one stdin chunk for the triple-Ctrl-C escape gesture (HATS-679).

    Pure function (no I/O) so the escalation logic is unit-testable without a
    PTY. ``presses`` carries Ctrl-C timestamps across calls and is mutated in
    place.

    Returns ``(forward, triggered)``:
      * ``forward`` — the bytes to write to the child: everything up to (but not
        including) the byte that trips the hatch. The triggering byte and the
        remainder of the chunk are withheld, so the 1st/2nd Ctrl-C still reach
        the child (dormancy, R2) but the 3rd does not.
      * ``triggered`` — ``True`` once ``count`` Ctrl-C bytes fall within
        ``window_s``.

    Counting is **per-byte**, not per-read: a batched chunk
    (``b"\\x03\\x03\\x03"``) trips on its 3rd byte; any non-Ctrl-C byte clears
    the streak (R2 "consecutive"); timestamps older than the window are dropped
    so a slow drip never accumulates. The sliding window ("N within the window")
    is deliberately stricter than a plain counter ("each gap < window").
    """
    for i, byte in enumerate(chunk):
        if byte == _ESCAPE_CTRL_C:
            presses.append(now)
            while presses and now - presses[0] > window_s:
                presses.popleft()
            if len(presses) >= count:
                presses.clear()
                return chunk[:i], True
        else:
            presses.clear()
    return chunk, False


def _cleanup_session_cache(project_dir: Path, session_id: str) -> None:
    """Remove the session's per-session cache dir (HATS-294).

    Drops the whole ``<ai_hats_dir>/.cache/sessions/<session_id>/`` tree
    (prompt.md + plugin/ + anything else providers stashed there).
    ``ignore_errors`` keeps us robust against repeated cleanup attempts,
    missing paths, and SIGKILL-orphans (TTL sweep mops those up later).
    """
    from .paths import session_cache_dir

    # Per-session cache: ephemeral, swept at session_end + TTL on next start.
    # Whitelist.
    shutil.rmtree(
        session_cache_dir(project_dir, session_id), ignore_errors=True
    )  # safe-delete: ok session-cache


def _session_timed_out(session: Session) -> bool:
    """True iff the session's metrics.json records ``timed_out: True``."""
    if not session.metrics_path.exists():
        return False
    try:
        metrics = json.loads(session.metrics_path.read_text())
    except (OSError, ValueError):
        return False
    return bool(metrics.get("timed_out"))


def _finalize_sub_agent(
    session: Session,
    *,
    role: str,
    model: str,
    isolation_mode: str,
    exit_code: int,
    # HATS-561: ``provider`` is keyword-only with a sentinel default so
    # legacy unit tests that exercise the function in isolation (and
    # don't care about provider — e.g. timeout / error / tags plumbing
    # tests) keep working without churn. EVERY production call site in
    # ``SubAgentRunner`` passes the real provider name explicitly; the
    # default is only the safety net for tests, NOT a fallback the
    # production code is allowed to rely on.
    provider: str = "unknown",
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    error: str | None = None,
    tags: dict[str, str] | None = None,
    duration_s: float | None = None,
    extra_metrics: dict | None = None,
    work_dir: Path | None = None,
    static_cost_analyzer=None,
    session_factory=None,
    audit_writer_factory=None,
) -> None:
    """Save transcripts and finalize audit with structured metrics.

    Called from every sub-agent terminal path (success, timeout, error) so
    session_dir is always consistently closed: transcript.txt + reasoning.log
    written if we have any output, metrics.json written with exit_code and
    optional timed_out/error/tags/duration_s fields. Provider-agnostic —
    behaves identically for claude and gemini.

    ``extra_metrics`` (HATS-474): provider-specific keys to merge into
    the metrics dict — e.g. ``claude_session_id``, ``total_cost_usd``,
    ``num_turns``, ``stop_reason`` from the Claude Agent SDK path.
    ``None`` values inside are skipped so legacy subprocess callers
    (Gemini) that have no such telemetry keep producing the same
    metrics.json shape they always did.

    ``work_dir`` (HATS-535): cwd the SDK ran under — encoded as
    claude's project_key when locating ``~/.claude/projects/<key>/
    <claude_session_id>.jsonl``. When provided alongside a
    ``claude_session_id`` in ``extra_metrics``, the
    ``finalize-subagent`` sub-pipeline runs and produces a structured
    ``audit.md`` (👤/👾/🔧/💭) for the SubAgent path — closing the
    HITL/Automate asymmetry that motivated HATS-535. Callers without
    ``work_dir`` (legacy / non-Claude subprocess paths)
    keep producing the meta-only ``audit.md`` they always did — opt-in
    enrichment, no behaviour change for the unfixed callsites.
    """
    if stdout:
        (session.session_dir / TRANSCRIPT_TXT).write_text(stdout)
    if stderr:
        (session.session_dir / REASONING_LOG).write_text(stderr)

    metrics: dict = {
        "exit_code": exit_code,
        "role": role,
        # HATS-561: provider was previously omitted from the SubAgent
        # finalize path's base metrics dict (only the HITL counterpart
        # `_finalize_session_basic` wrote it). The downstream
        # `AuditWriter._render_audit` then read `metrics.get("provider",
        # "unknown")` → audit.md said `Provider: unknown` for every
        # SubAgent / `execute --batch` session. Provider is known by
        # `SubAgentRunner` (its `CompositionPayload.provider`, HATS-865)
        # and is threaded through here.
        "provider": provider,
        "model": model,
        "isolation_mode": isolation_mode,
    }
    if timed_out:
        metrics["timed_out"] = True
    if error is not None:
        metrics["error"] = error
    if tags:
        metrics["tags"] = tags
    if duration_s is not None:
        metrics["duration_s"] = round(duration_s, 3)
    if extra_metrics:
        for k, v in extra_metrics.items():
            if v is not None:
                metrics[k] = v

    session.finalize_audit(metrics)

    # HATS-535: opt-in structured audit.md via finalize-subagent
    # sub-pipeline. Requires both work_dir (for JSONL project_key
    # encoding) and a claude_session_id (no claude jsonl without it —
    # e.g. Gemini provider has neither).
    claude_session_id = None
    if extra_metrics:
        claude_session_id = extra_metrics.get("claude_session_id")
    if work_dir is not None and claude_session_id:
        try:
            _run_finalize_subagent(
                session,
                claude_session_id=claude_session_id,
                project_dir=work_dir,
                exit_code=exit_code,
                static_cost_analyzer=static_cost_analyzer,
                session_factory=session_factory,
                audit_writer_factory=audit_writer_factory,
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("finalize-subagent pipeline failed", exc_info=True)


def _claude_jsonl_path(project_dir: Path, claude_session_id: str) -> Path | None:
    """Resolve path to Claude Code's JSONL conversation file."""
    return claude_transcript_path(project_dir, claude_session_id)


def _discover_claude_jsonl(project_dir: Path, session_id: str) -> Path | None:
    """Best-effort JSONL discovery when ``--session-id`` was not injected.

    HATS-272: in ``--resume``/``--continue`` mode the wrapper skips
    ``--session-id`` to avoid Claude CLI rejecting it. Our generated uuid
    therefore never reaches Claude, and the JSONL lives under Claude's
    own (different) uuid. Without this fallback ``AuditWriter`` walks
    the trace branch and emits zero-token metrics.

    Strategy: pick the most-recently-modified ``*.jsonl`` in the project's
    Claude dir whose mtime is at or after our session start. Single
    interactive session per project is the common case — heuristic is
    safe there. Concurrent wraps in the same project may misattribute;
    accepted limitation, single-session case is the fix target.
    """
    from datetime import datetime, timezone

    jsonl_dir = claude_transcripts_dir(project_dir)
    if not jsonl_dir.is_dir():
        return None
    try:
        start_ts = (
            datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S")
            .replace(
                tzinfo=timezone.utc,
            )
            .timestamp()
        )
    except (ValueError, IndexError):
        return None

    best: tuple[float, Path] | None = None
    for f in jsonl_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime < start_ts:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, f)
    return best[1] if best else None


def _highlight_hash(version: str) -> str:
    """Colorize only the git-hash local segment of a setuptools-scm version.

    ``0.8.1.dev127+gf7f916378`` → base + cyan ``gf7f916378``. A clean release
    (no ``+`` local segment) has nothing to highlight and returns verbatim.
    """
    base, sep, local = version.partition("+")
    if not sep:
        return version
    return f"{base}+\033[36m{local}\033[0m"


def _print_session_start(
    role: str,
    provider: str,
    session_id: str,
    *,
    version: str | None = None,
    channel: str | None = None,
) -> None:
    role_info = f"\033[1;36m{role or 'none'}\033[0m"
    provider_info = f"\033[1;35m{provider}\033[0m"
    line = f"\n[*] Role: {role_info} | Provider: {provider_info} | Session: {session_id}"
    if version:
        chan = f" ({channel})" if channel else ""
        line += f" | ai-hats v{_highlight_hash(version)}{chan}"
    print(line + "\n")


# ----- Pre-launch startup hold (HATS-825) -----
#
# The wrapped CLI's full-screen TUI tears the terminal into the alternate
# screen buffer the instant it spawns, clobbering anything ``run()`` printed
# before it — including any fail-open startup warning (git-hook resync,
# finalize preload). When a startup step warned, a brief hold gives the human
# a beat to read it, so a degraded session (hooks not synced, formatting hook
# unwired) is noticed *before* a session's worth of work runs against it — not
# in the post-mortem end-banner, which arrives too late to act on. A clean
# start does NOT hold: there is nothing to read, and the delay is pure friction
# (HATS-825 follow-up — the original 1s default was scrapped after use).

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
    """Render startup notices before the hold: ✓ notes (green) then ⚠ warns
    (yellow), each as a titled bullet block. Generalizes the warnings-only
    channel (HATS-825 → HATS-833) so a heal note shares the same visible,
    non-blocking surface as a warning."""
    notes = [n for n in notices if n.level == "note"]
    warns = [n for n in notices if n.level != "note"]
    g, y, rst = "\033[1;32m", "\033[1;33m", "\033[0m"
    if notes:
        print(f"{g}✓ {len(notes)} startup note(s):{rst}")
        for n in notes:
            print(f"{g}  • {n.text}{rst}")
    if warns:
        print(f"{y}⚠ {len(warns)} startup warning(s):{rst}")
        for n in warns:
            print(f"{y}  • {n.text}{rst}")


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


def _fmt_duration(session_id: str) -> str:
    from datetime import datetime, timezone

    try:
        start = datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
        return f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
    except Exception:
        return "?"


def _collect_trace_stats(session: "Session") -> dict:
    """Collect trace stats before cleanup may delete the file."""
    stats: dict = {"req_count": 0, "trace_size": 0}
    if session.trace_path.exists():
        text = session.trace_path.read_text()
        stats["req_count"] = text.count("[REQ]")
        stats["trace_size"] = session.trace_path.stat().st_size
    return stats


def _format_tokens(session: "Session") -> str:
    """Format the aggregated token usage line from metrics.json.

    Returns a single line with input/output tokens and cache hit/creation
    counts. Falls back to ``🪙 Tokens: n/a`` when the metrics file is missing,
    unreadable, or lacks a ``tokens`` block (e.g. non-Claude providers).
    """
    if not session.metrics_path.exists():
        return "🪙 Tokens: n/a"
    try:
        metrics = json.loads(session.metrics_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "🪙 Tokens: n/a"
    tokens = metrics.get("tokens")
    if not tokens:
        return "🪙 Tokens: n/a"
    tin = tokens.get("input", 0)
    tout = tokens.get("output", 0)
    cread = tokens.get("cache_read", 0)
    cnew = tokens.get("cache_creation", 0)
    return f"🪙 📥 {tin:,} in   📤 {tout:,} out   •   ♻️  {cread:,} hit   ✨ {cnew:,} new"


def _print_session_end(
    session: "Session",
    trace_stats: dict | None = None,
    retro: dict | None = None,
) -> None:
    """Render the green ``✨ Session <id> complete!`` summary.

    HATS-535: the **retro reminder banner** lines (cyan
    "Reflect through N sessions" + wrap-up nudge) used to print inline
    here. They now print at the tail of ``RunSessionEnd`` (in the
    ``finalize-hitl`` sub-pipeline), AFTER ``SESSION_END`` hooks fire.
    The ``retro`` parameter is retained for the one-line
    ``📝 Retro: <decision>`` summary that still belongs with the
    session-end banner — callers that have a retro decision in hand
    (e.g. the auto-retro reviewer's own banner) pass it; the standard
    HITL flow calls this with ``retro=None`` and the line is omitted.
    """
    if trace_stats is None:
        trace_stats = _collect_trace_stats(session)

    audit_info = "—"
    if session.audit_path.exists():
        audit_info = f"{session.audit_path.stat().st_size / 1024:.1f}KB"

    trace_size = trace_stats.get("trace_size", 0)
    trace_info = f"{trace_size / 1024:.1f}KB" if trace_size else "cleaned"
    req_count = trace_stats.get("req_count", 0)

    duration = _fmt_duration(session.session_id)

    # Clear any remnants of the CLI TUI (status bar, cursor position) before printing
    sys.stdout.write("\r\033[J\033[0m\n")
    sys.stdout.flush()
    print(f"\033[1;32m✨ Session {session.session_id} complete!\033[0m")
    print("━" * 52)
    print(f"  ⏱  {duration}   💬 {req_count} turns")
    print(f"  📄 Audit: {audit_info}   📊 Trace: {trace_info}")
    if retro is not None:
        try:
            from .retro.auto_retro import describe_decision

            print(f"  📝 Retro: {describe_decision(retro)}")
        except Exception:
            logger.warning("retro banner line failed", exc_info=True)
    print(f"  {_format_tokens(session)}")
    print(f"  📂 {session.session_dir}")
    print("━" * 52 + "\n")


def _finalize_session_basic(
    session: "Session",
    *,
    exit_code: int,
    active_role: str | None,
    provider_name: str,
    tracer: "SidecarTracer",
    tags: dict[str, str] | None = None,
) -> dict:
    """Per-runner minimal HITL finalize: log + metrics.json + smoke test.

    HATS-535: split from the legacy ``_finalize_session`` megafunction.
    Audit derivation (``AuditWriter``) + retro decision + SESSION_END
    hooks + reviewer spawn moved to the ``finalize-hitl`` sub-pipeline
    (``MakeAudit`` + ``RunSessionEnd``), invoked by the caller AFTER
    this function returns. ``_print_session_end`` is also caller-driven
    (must run in outer ``finally`` to surface the session id even on
    SIGINT).

    Each phase is wrapped in ``try/except (Exception, KeyboardInterrupt)``
    per the HATS-086 invariant — a second Ctrl+C must not kill cleanup
    partway. Returns ``trace_stats`` so the caller can thread it into
    ``_print_session_end`` without re-reading ``trace.log``.
    """
    trace_stats: dict = {}
    try:
        # HATS-529: Path A (live PTY ⏺-marker audit) removed. The
        # surrounding try/except is reserved as a scaffold for future
        # finalize-time tracer cleanup hooks — the HATS-086 SIGINT-safety
        # pattern (catch both Exception and KeyboardInterrupt so a second
        # Ctrl+C does not kill cleanup partway) is uniform across every
        # phase in this function, and re-introducing it later by hand is
        # error-prone. Leave the frame in place.
        _ = tracer  # silence unused-arg lint until a real cleanup lands
    except (Exception, KeyboardInterrupt):
        logger.warning("tracer cleanup failed", exc_info=True)

    try:
        session.log_trace(TraceTag.SYS, f"Session ended: exit_code={exit_code}")
        session.append_audit(f"Session ended with code {exit_code}")
    except (Exception, KeyboardInterrupt):
        logger.warning("session trace/audit append failed", exc_info=True)

    try:
        metrics: dict = {
            "exit_code": exit_code,
            "role": active_role,
            "provider": provider_name,
        }
        if tags:
            metrics["tags"] = tags
        session.finalize_audit(metrics)
    except (Exception, KeyboardInterrupt):
        logger.warning("audit finalization failed", exc_info=True)

    try:
        trace_stats = _collect_trace_stats(session)
    except (Exception, KeyboardInterrupt):
        logger.warning("trace stats collection failed", exc_info=True)

    # Smoke-test: non-error session should have turns after enrichment.
    # NB: enrichment now happens in ``MakeAudit`` (downstream); this
    # smoke test fires before that, so the warning is a no-op until the
    # finalize-hitl pipeline runs. Kept here for parity with the
    # pre-HATS-535 placement; the meaningful check is the metrics.json
    # state at session-end print time.
    try:
        if exit_code == 0 and session.metrics_path.exists():
            metrics = json.loads(session.metrics_path.read_text())
            if metrics.get("turns", 0) == 0:
                logger.debug(
                    "session %s: exit_code=0 but turns=0 pre-enrichment — "
                    "expected; MakeAudit will populate from JSONL",
                    session.session_id,
                )
    except (Exception, KeyboardInterrupt):
        pass

    return trace_stats


def _log_pipeline_errors(pipeline_name: str, final_state: dict) -> None:
    """Surface per-step errors swallowed by ``failure_policy=continue``.

    Pipeline runner records continue-policy failures in
    ``state["errors"]`` and proceeds to the next step (no exception
    raised). Without this hook the only visible signal is the outer
    catch's ``finalize-* pipeline failed`` line, which fires for
    HALT-policy crashes only. For continue-policy regressions (a step
    silently no-opping due to a fresh bug) we'd see nothing — hence the
    explicit drain.
    """
    errors = final_state.get(KEY_ERRORS) or {}
    for step_name, exc in errors.items():
        logger.warning(
            "%s step %s failed: %s: %s",
            pipeline_name,
            step_name,
            type(exc).__name__,
            exc,
        )


def _run_finalize_hitl(
    session: "Session",
    *,
    claude_session_id: str,
    project_dir: Path,
    exit_code: int,
    static_cost_analyzer=None,
    session_factory=None,
    audit_writer_factory=None,
) -> None:
    """Invoke the ``finalize-hitl`` sub-pipeline (HATS-535).

    The pipeline runs ``make_audit`` then ``run_session_end`` (retro banner).
    Caller (WrapRunner.run's finally) wraps this in its own try/except so a
    finalize-pipeline crash never blocks the outer ``_print_session_end``.
    ``static_cost_analyzer`` (HATS-865): runner-threaded carve-out so
    ``compute_usage`` can cross-check always-on cost without composing.
    """
    from .pipeline.loader import load_core_pipeline
    from .pipeline.pipeline import run as run_pipeline

    initial: dict = {
        KEY_SESSION_ID: session.session_id,
        KEY_SESSION_DIR: session.session_dir,
        KEY_CLAUDE_SESSION_ID: claude_session_id,
        KEY_PROJECT_DIR: project_dir,
        KEY_EXIT_CODE: exit_code,
    }
    if static_cost_analyzer is not None:
        initial["static_cost_analyzer"] = static_cost_analyzer
    # HATS-867: observe factories for make_audit — None-filtered (funnel v-contract).
    if session_factory is not None:
        initial["session_factory"] = session_factory
    if audit_writer_factory is not None:
        initial["audit_writer_factory"] = audit_writer_factory
    pipeline = load_core_pipeline(PIPELINE_FINALIZE_HITL)
    final_state = run_pipeline(pipeline, initial=initial)
    _log_pipeline_errors(PIPELINE_FINALIZE_HITL, final_state)


def _run_finalize_subagent(
    session: "Session",
    *,
    claude_session_id: str,
    project_dir: Path,
    exit_code: int,
    static_cost_analyzer=None,
    session_factory=None,
    audit_writer_factory=None,
) -> None:
    """Invoke the ``finalize-subagent`` sub-pipeline (HATS-535).

    Pipeline runs ``make_audit`` only — SubAgent path intentionally
    omits ``run_session_end`` to preserve pre-HATS-535 behaviour
    (no SESSION_END hooks, no auto-retro for sub-agents).
    """
    from .pipeline.loader import load_core_pipeline
    from .pipeline.pipeline import run as run_pipeline

    initial: dict = {
        KEY_SESSION_ID: session.session_id,
        KEY_SESSION_DIR: session.session_dir,
        KEY_CLAUDE_SESSION_ID: claude_session_id,
        KEY_PROJECT_DIR: project_dir,
        KEY_EXIT_CODE: exit_code,
    }
    if static_cost_analyzer is not None:
        initial["static_cost_analyzer"] = static_cost_analyzer
    # HATS-867: observe factories for make_audit — None-filtered (funnel v-contract).
    if session_factory is not None:
        initial["session_factory"] = session_factory
    if audit_writer_factory is not None:
        initial["audit_writer_factory"] = audit_writer_factory
    pipeline = load_core_pipeline(PIPELINE_FINALIZE_SUBAGENT)
    final_state = run_pipeline(pipeline, initial=initial)
    _log_pipeline_errors(PIPELINE_FINALIZE_SUBAGENT, final_state)
