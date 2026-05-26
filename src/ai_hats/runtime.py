"""Runtime — PTY wrapping, hooks execution, sub-agent launch."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from typing import TYPE_CHECKING

from .assembler import Assembler
from .harness.diagnostic import diagnose_silent_session
from .harness.errors import HarnessTimeoutError
from .harness.guard import apply_post_run_guard
from .materialize import compose_for_role
from .models import LifecycleEvent
from .observe import Session, SessionManager, SidecarTracer, TraceTag
from .paths import hooks_dir as _hooks_dir
from .providers import get_provider
from .pty_shutdown import bounded_proc_shutdown, emit_terminal_reset
from .worktree import IsolationMode, WorktreeManager

if TYPE_CHECKING:
    from .pipeline.harness_policy import HarnessPolicy

logger = logging.getLogger(__name__)

# Sub-agent subprocess wall-clock limit. Exceeding this raises TimeoutExpired,
# which is handled by graceful finalize (partial transcript + exit_code=124).
SUBAGENT_SUBPROCESS_TIMEOUT_S = 600

# Exit code conventions for early termination. 124 matches GNU coreutils `timeout`.
SUBAGENT_EXIT_TIMEOUT = 124
SUBAGENT_EXIT_ERROR = 1

# HATS-215 / HATS-220: emitted on stdout before each PTY child spawn to
# neutralise terminal-emulator state that a prior TUI session may have leaked.
# All sequences are idempotent on a clean terminal.
#
# HATS-220 evidence: Ghostty emitted `\n` for plain Enter in a session that
# had modifyOtherKeys=2 active (verified: Ctrl+J came through as the xterm
# extended-key form `\x1b[27;5;106~`). The original HATS-215 prelude did NOT
# reset modifyOtherKeys, so this state survived across sessions in one pane.
#
#   \x1b[=0;1u    — kitty-keyboard ABSOLUTE set: flags=0, mode=1 (replace).
#                   Replaces the relative `\x1b[<u` pop, which was unreliable
#                   because the stack could already be at depth 0 or below.
#   \x1b[>4;0m    — modifyOtherKeys=0 (reset to default). Was the leaking
#                   mode in HATS-220 — leaves Enter→\r encoding intact.
#   \x1b[20l      — LNM off (DEC ANSI mode 20). Defensive; some terminals
#                   leak it from prior `\x1b[20h`.
#   \x1b>         — DECKPNM (numeric keypad mode); ensures keypad Enter
#                   sends \r, not \x1bOM (DECKPAM application form).
#   \x1b[?2004l   — disable bracketed paste
#   \x1b[?1l      — exit application cursor mode (DECCKM off)
#   \x1b[?25h     — show cursor (in case prior TUI hid it and crashed)
_TERM_RESET_PRELUDE = (
    "\x1b[=0;1u"
    "\x1b[>4;0m"
    "\x1b[20l"
    "\x1b>"
    "\x1b[?2004l"
    "\x1b[?1l"
    "\x1b[?25h"
)


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
    shutil.rmtree(session_cache_dir(project_dir, session_id), ignore_errors=True)  # safe-delete: ok session-cache


def _sweep_orphan_session_caches(project_dir: Path, ttl_hours: int = 24) -> None:
    """Remove session cache dirs older than ttl_hours (HATS-294).

    Idempotent. Called once per ``Runtime.run`` invocation on session_start.
    Cheap when the cache root is empty or recent.
    """
    import time

    from .paths import session_cache_root

    root = session_cache_root(project_dir)
    if not root.exists():
        return
    cutoff = time.time() - ttl_hours * 3600
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)  # safe-delete: ok session-cache (TTL sweep)
        except OSError:
            pass


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
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    error: str | None = None,
    tags: dict[str, str] | None = None,
    duration_s: float | None = None,
    extra_metrics: dict | None = None,
    work_dir: Path | None = None,
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
    ``work_dir`` (multi-turn ``SubAgentSession`` flow, legacy paths)
    keep producing the meta-only ``audit.md`` they always did — opt-in
    enrichment, no behaviour change for the unfixed callsites.
    """
    if stdout:
        (session.session_dir / "transcript.txt").write_text(stdout)
    if stderr:
        (session.session_dir / "reasoning.log").write_text(stderr)

    metrics: dict = {
        "exit_code": exit_code,
        "role": role,
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
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("finalize-subagent pipeline failed", exc_info=True)


class HooksRunner:
    """Executes lifecycle hook scripts."""

    def __init__(self, hooks_dir: Path, project_dir: Path) -> None:
        self.hooks_dir = hooks_dir
        self.project_dir = project_dir

    def run(self, event: LifecycleEvent, env: dict[str, str] | None = None) -> list[dict]:
        """Run all hook scripts for an event. Returns list of results."""
        results = []
        scripts = self._find_scripts(event)
        for script in scripts:
            result = self._execute(script, env or {})
            results.append(result)
        return results

    def _find_scripts(self, event: LifecycleEvent) -> list[Path]:
        """Find hook scripts in hooks dir matching the event pattern."""
        scripts = []
        if not self.hooks_dir.exists():
            return scripts
        # Convention: scripts starting with event name or in event subdir
        for f in sorted(self.hooks_dir.iterdir()):
            if f.is_file() and f.suffix in (".sh", ".py") and f.stem.startswith(event.value):
                scripts.append(f)
        event_dir = self.hooks_dir / event.value
        if event_dir.is_dir():
            for f in sorted(event_dir.iterdir()):
                if f.is_file() and f.suffix in (".sh", ".py"):
                    scripts.append(f)
        return scripts

    def _execute(self, script: Path, env: dict[str, str]) -> dict:
        """Execute a single hook script."""
        full_env = {**os.environ, **env}
        try:
            result = subprocess.run(
                [str(script)],
                cwd=str(self.project_dir),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "script": str(script),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"script": str(script), "returncode": -1, "error": "timeout"}
        except Exception as e:
            return {"script": str(script), "returncode": -1, "error": str(e)}


def _claude_jsonl_path(project_dir: Path, claude_session_id: str) -> Path | None:
    """Resolve path to Claude Code's JSONL conversation file."""
    project_key = str(project_dir).replace("/", "-")
    return Path.home() / ".claude" / "projects" / project_key / f"{claude_session_id}.jsonl"


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

    project_key = str(project_dir).replace("/", "-")
    jsonl_dir = Path.home() / ".claude" / "projects" / project_key
    if not jsonl_dir.is_dir():
        return None
    try:
        start_ts = datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(
            tzinfo=timezone.utc,
        ).timestamp()
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


def _print_session_start(role: str, provider: str, session_id: str) -> None:
    role_info = f"\033[1;36m{role or 'none'}\033[0m"
    provider_info = f"\033[1;35m{provider}\033[0m"
    print(f"\n[*] Role: {role_info} | Provider: {provider_info} | Session: {session_id}\n")


def _composition_snapshot(assembler: Assembler, role_name: str, result) -> dict:
    """Build the composition snapshot dict for ``Session.init_audit`` (HATS-442).

    Returns a dict with effective ``traits``/``rules``/``skills`` lists plus
    a ``provenance`` map tagging each name with the contributing layer
    (``built-in``/``global``/``project``). Used by session-reviewer and any
    other post-session consumer to know what actually loaded in a session.

    The traits list is computed by walking the role's base composition and
    re-applying overlays (mirroring ``_build_tree``); composer's output
    already has the resolved rules/skills.
    """
    try:
        base_cfg = assembler.resolver.resolve_role_config(role_name)
        effective_traits: list[str] = list(base_cfg.composition.traits) if base_cfg else []
        for layer in (
            assembler._get_global_overlay(role_name),
            assembler._get_overlay(role_name),
        ):
            if layer is None:
                continue
            for name in layer.remove_traits:
                if name in effective_traits:
                    effective_traits.remove(name)
            for name in layer.add_traits:
                if name not in effective_traits:
                    effective_traits.append(name)
        provenance = assembler._get_overlay_provenance(role_name)
    except Exception:
        # Defensive: a broken overlay shouldn't kill session start. Fall
        # back to "no snapshot" — audit.md just won't have the section.
        return {}
    return {
        "traits": effective_traits,
        "rules": [r.name for r in result.rules],
        "skills": [s.name for s in result.skills],
        "provenance": provenance,
    }


def _fmt_duration(session_id: str) -> str:
    from datetime import datetime, timezone
    try:
        start = datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(
            tzinfo=timezone.utc
        )
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
    errors = final_state.get("errors") or {}
    for step_name, exc in errors.items():
        logger.warning(
            "%s step %s failed: %s: %s",
            pipeline_name, step_name, type(exc).__name__, exc,
        )


def _run_finalize_hitl(
    session: "Session",
    *,
    claude_session_id: str,
    project_dir: Path,
    env: dict[str, str],
    exit_code: int,
) -> None:
    """Invoke the ``finalize-hitl`` sub-pipeline (HATS-535).

    The pipeline runs ``make_audit`` then ``run_session_end``. Caller
    (WrapRunner.run's finally) wraps this in its own try/except so a
    finalize-pipeline crash never blocks the outer
    ``_print_session_end``.
    """
    from .pipeline.loader import load_core_pipeline
    from .pipeline.pipeline import run as run_pipeline

    pipeline = load_core_pipeline("finalize-hitl")
    final_state = run_pipeline(pipeline, initial={
        "session_id": session.session_id,
        "session_dir": session.session_dir,
        "claude_session_id": claude_session_id,
        "project_dir": project_dir,
        "exit_code": exit_code,
        "hooks_env": env,
    })
    _log_pipeline_errors("finalize-hitl", final_state)


def _run_finalize_subagent(
    session: "Session",
    *,
    claude_session_id: str,
    project_dir: Path,
    exit_code: int,
) -> None:
    """Invoke the ``finalize-subagent`` sub-pipeline (HATS-535).

    Pipeline runs ``make_audit`` only — SubAgent path intentionally
    omits ``run_session_end`` to preserve pre-HATS-535 behaviour
    (no SESSION_END hooks, no auto-retro for sub-agents).
    """
    from .pipeline.loader import load_core_pipeline
    from .pipeline.pipeline import run as run_pipeline

    pipeline = load_core_pipeline("finalize-subagent")
    final_state = run_pipeline(pipeline, initial={
        "session_id": session.session_id,
        "session_dir": session.session_dir,
        "claude_session_id": claude_session_id,
        "project_dir": project_dir,
        "exit_code": exit_code,
    })
    _log_pipeline_errors("finalize-subagent", final_state)


class WrapRunner:
    """PTY-proxied CLI wrapper for interactive sessions."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def _make_session_hooks_runner(self) -> HooksRunner:
        """Build the session lifecycle ``HooksRunner`` against the canonical
        hooks dir (``<ai_hats_dir>/library/hooks/``).

        Extracted from ``run()`` for HATS-412 testability — the original
        inline construction hard-coded the legacy ``.agent/hooks/`` path
        which has been empty since HATS-314 deleted that tree, so
        lifecycle hooks silently never fired. Having a single helper
        means future call-sites can't drift back to the legacy path.
        """
        return HooksRunner(_hooks_dir(self.project_dir), self.project_dir)

    def run(
        self,
        provider_name: str,
        role_override: str | None = None,
        extra_args: list[str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> tuple[int, Session]:
        """Launch a wrapped CLI session with PTY proxying.

        Returns (exit_code, session) so callers that need the session
        artefacts (transcript_path, audit, etc.) get them directly. The
        legacy ``int``-only contract is preserved by ``_do_execute`` which
        still returns only the exit code to its CLI callers.

        HATS-452 (П2 in ADR-0005). ``WrapRunner`` is the **HITL** runner —
        a human is at the keyboard and the role's full composition reaches
        the agent through the composer + ``build_session_prompt`` write.
        It deliberately has **no** ``system_prompt_override`` channel:
        prompt injection in HITL is meaningless (the user types into the
        terminal) and the previously-exposed Optional override was the
        literal trap that caused HATS-452. Callers needing to inject an
        explicit prompt should use ``SubAgentRunner`` (Automate path),
        which takes a required ``task`` argument.
        """
        # Resolve provider
        provider = get_provider(provider_name)

        # Determine which role to use
        cfg = self.assembler.project_config
        effective_role = role_override or cfg.active_role or cfg.default_role

        # HATS-294: unified per-session compose. Every session — default role
        # and explicit --role alike — goes through build_session_prompt. The
        # composed prompt is written to a per-session temp file (no shared
        # canonical role-content). set_role still runs on first-run /
        # provider-switch to sync project_config.active_role, but the session
        # itself no longer depends on the canonical layout on disk.
        if effective_role and not role_override:
            needs_assembly = (
                not cfg.active_role  # no role set yet
                or cfg.provider != provider_name  # provider mismatch
            )
            if needs_assembly:
                self.assembler.set_role(effective_role, provider_name)
                cfg = self.assembler.project_config  # reload

        active_role = role_override or cfg.active_role

        # HATS-294: sweep stale session cache dirs (>24h old) before allocating
        # a new sid. Idempotent and cheap when cache root is empty.
        _sweep_orphan_session_caches(self.project_dir)

        # Create session — must happen before build_session_prompt so we can
        # key the per-session cache dir on session.session_id (HATS-294).
        session = self.session_mgr.create_session()

        # HATS-456: single derivation point for "compose for role X".
        # HATS-452 (П2): no override channel on WrapRunner — the composition
        # produced here flows straight into ``build_session_prompt``.
        result = compose_for_role(self.assembler, effective_role)
        session_args, session_env, meta_prompt = provider.build_session_prompt(
            self.project_dir, result, session.session_id,
        )
        session.init_audit(
            role=active_role,
            provider=provider_name,
            composition=_composition_snapshot(self.assembler, effective_role, result),
        )
        # HATS-523: persist materialized system prompt to
        # <session_dir>/meta_prompt.txt — symmetric with SubAgentRunner
        # (runtime.py ~1091). Exact bytes that reached the provider (post
        # HATS-380 placeholder expansion). Saved before hooks / _pty_spawn so
        # the artefact survives early failures.
        session.save_meta_prompt(meta_prompt)
        session.log_trace(TraceTag.SYS, f"Session started: role={active_role}")

        # Log CLI restart gap from previous session (helps judge distinguish
        # restarts from provider stalls).
        self._log_restart_gap(session)

        # Build environment
        env = {
            **os.environ,
            **session.get_env(),
            **provider.get_env(session.session_dir, self.project_dir),
            **session_env,
            "AI_HATS_ROLE": active_role,
        }

        # Run hooks: session_start. See _make_session_hooks_runner for the
        # canonical-path rationale (HATS-412).
        hooks_runner = self._make_session_hooks_runner()
        hooks_runner.run(LifecycleEvent.SESSION_START, env=env)
        session.log_trace(TraceTag.SYS, "hooks.session_start completed")

        # Build CLI command with session ID for JSONL linkage
        claude_session_id = str(uuid.uuid4())
        cmd = provider.get_cli_command(extra_args)
        cmd.extend(session_args)
        # Don't inject --session-id when the user is resuming/continuing
        # an existing session — it already has its own id, and Claude CLI
        # rejects --session-id + --resume without --fork-session.
        _resuming = extra_args and any(
            f in extra_args for f in ("--resume", "--continue", "-c")
        )
        if provider_name == "claude" and not _resuming:
            cmd += ["--session-id", claude_session_id]
        session.log_trace(TraceTag.SYS, f"Launching: {' '.join(cmd)}")
        session.append_audit(f"Launched {provider_name} CLI")

        _print_session_start(active_role, provider_name, session.session_id)

        # PTY proxy via pty.spawn with sidecar trace.
        # HATS-086: wrap _pty_spawn so SIGINT during the interactive part
        # routes through the finalize chain in the finally block, ensuring
        # the session-end summary (with the all-important session id) is
        # always printed.
        #
        # HATS-535: finalize is now a three-step chain:
        #   1. _finalize_session_basic — metrics.json, trace stats, smoke
        #   2. finalize-hitl pipeline — make_audit + run_session_end
        #   3. _print_session_end — green summary (outer finally; SIGINT-safe)
        # Each layer's exceptions are isolated so a downstream crash
        # never prevents the session-id print (HATS-086 invariant).
        tracer = SidecarTracer(session)
        exit_code = 130  # canonical SIGINT default if _pty_spawn raises pre-assignment
        try:
            exit_code = self._pty_spawn(cmd, env, tracer)
        except KeyboardInterrupt:
            exit_code = 130
        finally:
            trace_stats: dict = {}
            try:
                trace_stats = _finalize_session_basic(
                    session,
                    exit_code=exit_code,
                    active_role=active_role,
                    provider_name=provider_name,
                    tracer=tracer,
                    tags=tags,
                )
                try:
                    _run_finalize_hitl(
                        session,
                        claude_session_id=claude_session_id,
                        project_dir=self.project_dir,
                        env=env,
                        exit_code=exit_code,
                    )
                except (Exception, KeyboardInterrupt):
                    logger.warning("finalize-hitl pipeline failed", exc_info=True)
            finally:
                # The summary print is the only thing that surfaces the
                # session id to the user. It MUST run, even on second
                # SIGINT, even if every step above failed.
                try:
                    _print_session_end(session, trace_stats=trace_stats)
                except (Exception, KeyboardInterrupt):
                    logger.warning("session-end print failed", exc_info=True)
                    try:
                        print(f"\n✨ Session {session.session_id} complete!")
                    except (BrokenPipeError, OSError):
                        pass

            # HATS-294: drop the per-session cache dir (prompt + plugin/).
            # SIGKILL-orphans are accepted — TTL sweep mops them up on the
            # next session_start.
            _cleanup_session_cache(self.project_dir, session.session_id)

        return exit_code, session

    def _log_restart_gap(self, session: Session) -> None:
        """If there's a recent previous session, log the gap as a CLI restart event."""
        from datetime import datetime, timezone
        try:
            all_sessions = self.session_mgr.list_sessions()
            # Need at least 2 sessions (current + previous)
            if len(all_sessions) < 2:
                return
            prev = all_sessions[-2]
            # Parse timestamps from session IDs
            fmt = "%Y%m%d-%H%M%S"
            prev_start = datetime.strptime(prev.session_id[:15], fmt).replace(tzinfo=timezone.utc)
            cur_start = datetime.strptime(session.session_id[:15], fmt).replace(tzinfo=timezone.utc)
            gap_secs = int((cur_start - prev_start).total_seconds())
            if gap_secs < 7200:  # Only note restarts within 2 hours
                if gap_secs >= 60:
                    gap_str = f"{gap_secs // 60}m {gap_secs % 60}s"
                else:
                    gap_str = f"{gap_secs}s"
                session.append_audit(f"🔄 CLI restarted — {gap_str} since previous session")
                session.log_trace(TraceTag.SYS, f"CLI restart gap: {gap_str}")
        except (ValueError, IndexError, OSError):
            logger.debug("CLI restart-gap detection failed", exc_info=True)

    def _pty_spawn(self, cmd: list[str], env: dict[str, str], tracer: SidecarTracer) -> int:
        """Spawn a process with PTY for interactive terminal passthrough + sidecar trace.

        Uses ptyprocess so the slave-pty becomes the controlling-tty of the child
        session (TIOCSCTTY in child after setsid). This is required for nested
        programs (e.g. claude → $EDITOR via Ctrl-G) whose pgrp transfer relies on
        kernel-side tcsetpgrp/setpgid against a real ctty. stdlib pty.spawn does
        not call TIOCSCTTY, which broke that path. See HATS-207.
        """
        import select
        import signal
        import termios
        import tty

        from ptyprocess import PtyProcess

        for k, v in env.items():
            os.environ[k] = v

        # HATS-215: defensive reset of DEC private modes that the previous
        # session may have leaked. Without this, leftover state (notably the
        # kitty-keyboard stack push left by an Ink-based TUI on ungraceful
        # exit) makes Enter encode as `\x1b[13u` in the next session — Claude
        # then treats Enter as Shift+Enter and inserts a newline instead of
        # submitting. Idempotent on a clean terminal.
        sys.stdout.write(_TERM_RESET_PRELUDE)
        sys.stdout.flush()

        # NOTE: os.get_terminal_size() returns (columns, lines), NOT (rows, cols).
        # ptyprocess expects dimensions=(rows, cols). Unpacking blindly would
        # transpose the window — claude TUI then renders into the wrong shape
        # (often a narrow strip) and fails to draw the input box / alt-screen.
        try:
            term_size = os.get_terminal_size()
            rows, cols = term_size.lines, term_size.columns
        except OSError:
            rows, cols = 24, 80

        try:
            proc = PtyProcess.spawn(cmd, dimensions=(rows, cols))
        except FileNotFoundError:
            print(f"Error: '{cmd[0]}' not found. Is it installed?", file=sys.stderr)
            return 127
        except OSError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        # Use raw fd constants (not sys.stdin/stdout.fileno()) so test harnesses
        # that wrap sys.stdin/stdout still pass through to the real terminal —
        # mirrors stdlib pty.spawn behaviour.
        master_fd = proc.fd
        stdin_fd = 0
        stdout_fd = 1
        master_read = tracer.make_master_read()
        stdin_read = tracer.make_stdin_read()

        def _on_winch(_sig, _frm):
            try:
                size = os.get_terminal_size()
                proc.setwinsize(size.lines, size.columns)
            except OSError:
                pass

        prev_winch = signal.signal(signal.SIGWINCH, _on_winch)

        restore_attrs = False
        old_attrs = None
        try:
            old_attrs = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
            restore_attrs = True
        except termios.error:
            pass

        # Drop stdin_fd from the read-set on EOF (pytest harness, redirected
        # input) without breaking the loop — child may still be producing
        # output that we need to drain until master EOF.
        read_fds = [master_fd, stdin_fd]
        try:
            while True:
                try:
                    rlist, _, _ = select.select(read_fds, [], [])
                except (OSError, select.error):
                    break

                if master_fd in rlist:
                    try:
                        data = master_read(master_fd)
                    except OSError:
                        break
                    if not data:
                        break
                    try:
                        os.write(stdout_fd, data)
                    except OSError:
                        break

                if stdin_fd in rlist:
                    # HATS-220: self-heal termios drift on parent stdin.
                    # Production session 175557 captured two consecutive Enter
                    # presses in the same Claude session: first arrived as \r
                    # (working), second as \n (broken submit). Mechanism: the
                    # tmux-pane slave PTY had ICRNL re-enabled by something
                    # mid-session, and the kernel translated the real \r into
                    # \n before our read(). Claude TUI then treated \n as
                    # newline-in-input instead of submit. Restoring raw mode
                    # before each stdin read costs ~2 syscalls and is
                    # idempotent when termios is already raw. Verified via
                    # /tmp/test_icrnl_fix.py: ICRNL=on yields \n; with this
                    # self-heal the same keypress yields \r.
                    if restore_attrs:
                        try:
                            cur = termios.tcgetattr(stdin_fd)
                            if cur[0] & (termios.ICRNL | termios.INLCR | termios.IGNCR):
                                tty.setraw(stdin_fd)
                                tracer.session.log_trace(
                                    TraceTag.SYS,
                                    f"HATS-220 termios drift on stdin (iflag={cur[0]:#x}) — restored raw",
                                )
                        except termios.error:
                            pass
                    try:
                        data = stdin_read(stdin_fd)
                    except OSError:
                        read_fds = [master_fd]
                        continue
                    if not data:
                        read_fds = [master_fd]
                        continue
                    try:
                        os.write(master_fd, data)
                    except OSError:
                        break
        finally:
            if restore_attrs and old_attrs is not None:
                try:
                    termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
                except termios.error:
                    pass
            try:
                signal.signal(signal.SIGWINCH, prev_winch)
            except (ValueError, OSError):
                pass
            # HATS-411: bounded shutdown — ptyprocess.wait() blocks on
            # os.waitpid(pid, 0) which hangs forever when the child is
            # stuck in macOS exit-pending state (`?Es`, libuv handle
            # leak). Escalate grace → SIGTERM-pgroup → SIGKILL → WNOHANG
            # reap so the parent always returns within bounded time.
            bounded_proc_shutdown(proc)
            # After the child is gone, clear mouse-tracking DECSETs on
            # the OUTER terminal (parent stdout) — prevents raw SGR
            # mouse reports from rendering as text in the surrounding
            # shell when the child crashed without disabling them.
            emit_terminal_reset(stdout_fd)

        if proc.exitstatus is not None:
            return int(proc.exitstatus)
        if proc.signalstatus is not None:
            return 128 + int(proc.signalstatus)
        # HATS-411: bounded_proc_shutdown could not confirm clean exit
        # (child stuck in `?Es` — WNOHANG reap returned (0, 0)). Surface
        # this as 124 (GNU coreutils `timeout` convention, also used by
        # SUBAGENT_EXIT_TIMEOUT) instead of silently returning success.
        return 124


class SubAgentRunner:
    """SDK-based sub-agent executor."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def run(
        self,
        role_name: str,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
        isolation_mode: str = "discard",
        tags: dict[str, str] | None = None,
        system_prompt_override: str | None = None,
        harness_policy: "HarnessPolicy | None" = None,
    ) -> Session:
        """Execute a sub-agent in isolation.

        ``system_prompt_override`` (HATS-267): when supplied, replaces the
        merged injection in the meta-prompt build while keeping structural
        composition data intact for provider-specific overrides.

        ``harness_policy`` (HATS-378): optional post-run reliability
        policy. When ``on_timeout`` is set, a subprocess timeout triggers
        retry-with-increased-budget up to ``retry`` extra attempts; on
        final timeout raises :class:`HarnessTimeoutError`. When
        ``reporting`` is set, the zero-output guard fires after a clean
        run. ``None`` preserves pre-HATS-378 behaviour (timeout returns
        a session with ``timed_out=True``; no zero-output check).
        """
        on_timeout = (
            harness_policy.on_timeout if harness_policy is not None else None
        )
        max_attempts = 1 + (on_timeout.retry if on_timeout is not None else 0)

        last_session: Session | None = None
        for attempt in range(1, max_attempts + 1):
            if attempt == 1 or on_timeout is None:
                timeout_s = SUBAGENT_SUBPROCESS_TIMEOUT_S
            else:
                timeout_s = int(
                    SUBAGENT_SUBPROCESS_TIMEOUT_S * on_timeout.budget_multiplier
                )
            attempt_tags = dict(tags or {})
            if attempt > 1:
                attempt_tags["harness_retry_attempt"] = str(attempt)

            last_session = self._run_attempt(
                role_name=role_name,
                task=task,
                ticket_id=ticket_id,
                model=model,
                parent_session=parent_session,
                isolation_mode=isolation_mode,
                tags=attempt_tags,
                system_prompt_override=system_prompt_override,
                timeout_s=timeout_s,
            )
            if not _session_timed_out(last_session):
                break  # success or non-timeout error — retry loop done

        assert last_session is not None  # loop body always assigns

        # Timeout policy: if final attempt still timed out and we had a
        # policy in place, escalate. Without a policy, preserve the
        # legacy behaviour: return the session with timed_out=True.
        if on_timeout is not None and _session_timed_out(last_session):
            raise HarnessTimeoutError(
                last_session.session_id,
                diagnose_silent_session(last_session),
            )

        # Zero-output guard: no-op when policy is None or reporting is
        # off. For sub-agents without trace-derived tokens/tool_calls in
        # metrics, the guard is also a no-op (see is_zero_output) — future
        # sub-agent metrics enrichment lights it up automatically.
        apply_post_run_guard(last_session, harness_policy)

        return last_session

    def _run_attempt(
        self,
        *,
        role_name: str,
        task: str,
        ticket_id: str,
        model: str,
        parent_session: str | None,
        isolation_mode: str,
        tags: dict[str, str],
        system_prompt_override: str | None,
        timeout_s: int,
    ) -> Session:
        """One sub-agent attempt — always finalizes metrics, never re-raises.

        Two execution engines live behind this entry point:

        * **Claude** path (HATS-474): :class:`claude_agent_sdk.ClaudeSDKClient`
          via :mod:`ai_hats.sdk_runner`. Wall-clock cap implemented as
          ``asyncio.wait_for(timeout_s)``; the helper never raises and
          always returns an :class:`SdkRunResult` we finalize from.

        * **Legacy subprocess** path (Gemini, future providers): unchanged
          ``subprocess.run`` flow. ``subprocess.TimeoutExpired`` keeps its
          long-standing finalize semantics here.

        Timeout and other failure modes are surfaced via metrics fields
        (``timed_out``, ``error``, ``exit_code``) so the outer retry loop
        can inspect them without exception plumbing.
        """
        session = self.session_mgr.create_session(parent_session=parent_session)

        # HATS-456: single derivation point for "compose for role X".
        # Override (HATS-267) stays on the Automate path per ADR-0005 П2.
        result = compose_for_role(self.assembler, role_name)
        # HATS-505 / HATS-452-class trap warning:
        # ``with_injection_override`` REPLACES ``result.injections``
        # WHOLESALE — every overlay-layer contribution (global + project
        # ``injection_append`` text, ``add_traits`` injection bodies)
        # gets dropped from the SDK system_prompt. The pipeline
        # (``LaunchProvider``) no longer feeds an override here
        # (HATS-505 (a)); the only legitimate caller is a HATS-267
        # explicit-prompt invocation (e.g. ``subagent_session.py`` or a
        # direct API consumer). If you add a new caller, the override
        # text MUST already contain everything the role would have
        # composed — or compose the role first and pass an *augmented*
        # (not replacement) string. Layered composition is in
        # ``result`` above this line if you need to read it.
        if system_prompt_override is not None:
            # HATS-452: explicit immutable transformation via the typed
            # ``with_*`` API on ``CompositionResult`` (П1 in ADR-0005).
            result = result.with_injection_override(system_prompt_override)
        provider_name = self.assembler.project_config.provider
        provider = get_provider(provider_name)

        # HATS-474: for the Claude path the meta-prompt stored on disk is
        # a *forensic* artifact — it records what we actually sent to the
        # SDK (system_prompt + initial user message), not what the legacy
        # ``-p`` arg would have looked like. For non-Claude providers we
        # keep the legacy structure intact.
        if provider_name == "claude":
            meta_prompt = self._build_sdk_prompt_audit(
                result=result, task=task, ticket_id=ticket_id,
            )
        else:
            meta_prompt = self._build_meta_prompt(
                result=result, provider=provider, task=task, ticket_id=ticket_id,
            )
        session.save_meta_prompt(meta_prompt)
        session.init_audit(
            role=role_name,
            provider=provider.name,
            model=model,
            composition=_composition_snapshot(self.assembler, role_name, result),
        )
        session.log_trace(TraceTag.SUB, f"Sub-agent started: role={role_name}")

        # HATS-474 review fix: keep the env we pass to a *subprocess* (Gemini
        # path) as the full inherited environment — subprocess.run replaces
        # the child env wholesale when given. The SDK path uses an *overlay*
        # via ClaudeAgentOptions.env, which the SDK merges on top of
        # os.environ at spawn time, so we hand it only ai-hats-specific
        # keys to avoid widening the secret-exposure surface (the SDK
        # stores options on a long-lived object, repr-able).
        env = {
            **os.environ,
            **session.get_env(),
            "AI_HATS_ROLE": role_name,
        }
        sdk_env_overlay = {
            **session.get_env(),
            "AI_HATS_ROLE": role_name,
        }

        # Legacy subprocess path still needs cmd / skill_args precomputed.
        # The Claude SDK path materializes skills internally via
        # ``build_options`` → ``_build_plugins``, so we skip the upfront
        # ``materialize_runtime_skills`` call when provider is claude
        # (the cache dir is produced inside the SDK builder instead).
        cmd: list[str] = []
        if provider_name != "claude":
            cmd = provider.get_cli_command()
            # HATS-307: materialize spawned role's skills for the sub-agent.
            # For Gemini this is currently a no-op (HATS-367 follow-up).
            # Cleaned by _cleanup_session_cache in the finally block.
            skill_args = provider.materialize_runtime_skills(
                self.project_dir, result, session.session_id,
            )
            cmd = cmd + skill_args
            session.log_trace(TraceTag.SUB, f"Executing: {' '.join(cmd)}")

        mode = IsolationMode(isolation_mode)
        session.log_trace(TraceTag.SUB, f"Isolation: {mode.value}")

        with WorktreeManager(
            self.project_dir, role_name, session.session_id, mode,
        ) as work_dir:
            session.log_trace(TraceTag.SUB, f"Working directory: {work_dir}")
            t0 = time.monotonic()
            try:
                if provider_name == "claude":
                    # HATS-474 Phase 2: SDK engine.
                    run_result = self._run_via_sdk(
                        result=result,
                        work_dir=work_dir,
                        session_id=session.session_id,
                        task=task,
                        ticket_id=ticket_id,
                        env=sdk_env_overlay,
                        model=model,
                        timeout_s=timeout_s,
                    )
                    session.log_trace(
                        TraceTag.RES, f"Exit code: {run_result.exit_code}",
                    )
                    if run_result.claude_session_id:
                        session.log_trace(
                            TraceTag.SUB,
                            f"Claude session_id: {run_result.claude_session_id}",
                        )
                    _finalize_sub_agent(
                        session,
                        role=role_name,
                        model=model,
                        isolation_mode=mode.value,
                        exit_code=run_result.exit_code,
                        stdout=run_result.stdout,
                        stderr=run_result.stderr,
                        timed_out=run_result.timed_out,
                        error=run_result.error,
                        tags=tags,
                        duration_s=time.monotonic() - t0,
                        extra_metrics={
                            "claude_session_id": run_result.claude_session_id,
                            "total_cost_usd": run_result.total_cost_usd,
                            "num_turns": run_result.num_turns,
                            "stop_reason": run_result.stop_reason,
                        },
                        work_dir=work_dir,
                    )
                else:
                    # Legacy subprocess path (Gemini and future non-SDK providers).
                    full_cmd = provider.get_run_command(
                        cmd, meta_prompt, model=model or None,
                    )
                    proc = subprocess.run(
                        full_cmd,
                        cwd=str(work_dir),
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                    )
                    session.log_trace(TraceTag.RES, f"Exit code: {proc.returncode}")
                    _finalize_sub_agent(
                        session,
                        role=role_name,
                        model=model,
                        isolation_mode=mode.value,
                        exit_code=proc.returncode,
                        stdout=proc.stdout or "",
                        stderr=proc.stderr or "",
                        tags=tags,
                        duration_s=time.monotonic() - t0,
                        work_dir=work_dir,
                    )

            except subprocess.TimeoutExpired as exc:
                session.log_trace(
                    TraceTag.SYS,
                    f"Sub-agent timed out after {timeout_s}s",
                )
                _finalize_sub_agent(
                    session,
                    role=role_name,
                    model=model,
                    isolation_mode=mode.value,
                    exit_code=SUBAGENT_EXIT_TIMEOUT,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                    timed_out=True,
                    tags=tags,
                    duration_s=time.monotonic() - t0,
                    work_dir=work_dir,
                )
            except Exception as e:
                # Catches any unanticipated SDK-path exception too — defence
                # in depth. ``run_claude_sdk_blocking`` is designed not to
                # raise, but ``asyncio.run`` itself can fail in weird envs
                # (running event loop, etc.) — surface as a clean error.
                session.log_trace(TraceTag.SYS, f"Sub-agent error: {e}")
                _finalize_sub_agent(
                    session,
                    role=role_name,
                    model=model,
                    isolation_mode=mode.value,
                    exit_code=SUBAGENT_EXIT_ERROR,
                    error=str(e),
                    tags=tags,
                    duration_s=time.monotonic() - t0,
                    work_dir=work_dir,
                )
            finally:
                _cleanup_session_cache(self.project_dir, session.session_id)

        return session

    # ----- HATS-474 helpers -----

    def _run_via_sdk(
        self,
        *,
        result,
        work_dir: Path,
        session_id: str,
        task: str,
        ticket_id: str,
        env: dict[str, str],
        model: str,
        timeout_s: int,
    ):
        """Drive the SDK path for one sub-agent attempt.

        Composes :class:`ClaudeAgentOptions` from the role result and runs
        the SDK under a wall-clock cap. Never raises — returns an
        :class:`SdkRunResult` for every terminal path (success, SDK error,
        timeout) so the caller's finalize logic is uniform.
        """
        from .sdk_options import build_first_user_message, build_options
        from .sdk_runner import run_claude_sdk_blocking

        ticket_context = self._load_ticket(ticket_id)
        project_state = self._read_project_state()

        options = build_options(
            result,
            project_dir=self.project_dir,
            session_id=session_id,
            work_dir=work_dir,
            model=model or "",
            extra_env=env or None,
        )
        initial_message = build_first_user_message(
            ticket_context=ticket_context,
            task=task,
            project_state=project_state,
        )
        return run_claude_sdk_blocking(
            options=options,
            initial_message=initial_message,
            timeout_s=timeout_s,
        )

    def _build_sdk_prompt_audit(
        self,
        *,
        result,
        task: str,
        ticket_id: str,
    ) -> str:
        """Render a human-readable artifact of what the SDK was actually sent.

        Saved alongside the session as ``meta_prompt.txt`` (same path the
        legacy subprocess path used) so audit / debugging tooling that
        relies on that file keeps working. The structure mirrors the two
        SDK inputs: the appended part of ``system_prompt`` and the first
        user message.
        """
        from .sdk_options import _build_system_prompt, build_first_user_message

        sp = _build_system_prompt(result, self.project_dir)
        system_text = sp.get("append", "")
        initial_message = build_first_user_message(
            ticket_context=self._load_ticket(ticket_id),
            task=task,
            project_state=self._read_project_state(),
        )
        return (
            "==== SDK system_prompt (preset=claude_code, append) ====\n"
            f"{system_text}\n"
            "\n"
            "==== SDK first user message ====\n"
            f"{initial_message}\n"
        )

    def _read_project_state(self) -> str:
        """Read ``state.md`` for the SDK path's first user message."""
        from .paths import state_md_path

        state_md = state_md_path(self.project_dir)
        return state_md.read_text() if state_md.exists() else ""

    def _build_meta_prompt(self, result, provider, task: str, ticket_id: str) -> str:
        """Build the meta-prompt for sub-agent execution."""
        from .paths import state_md_path
        from .placeholders import expand_path_placeholders

        sections = []

        # SYSTEM_ROLE — HATS-380: expand <ai_hats_dir> before the role/trait
        # injection reaches the sub-agent inline. Canonical writer and provider
        # build_session_prompt paths already expand; meta-prompt was the residual gap
        # (roles like session-reviewer carry literal <ai_hats_dir> in injection).
        merged = expand_path_placeholders(result.merged_injection, self.project_dir)
        sections.append(f"# SYSTEM_ROLE\n{merged}")

        # PROJECT_STATE
        state_md = state_md_path(self.project_dir)
        if state_md.exists():
            sections.append(f"# PROJECT_STATE\n{state_md.read_text()}")

        # CONSTRAINTS
        if result.priorities:
            constraints = "\n".join(f"- {p}" for p in result.priorities)
            sections.append(f"# CONSTRAINTS\n{constraints}")

        # TICKET_CONTEXT
        if ticket_id:
            ticket_context = self._load_ticket(ticket_id)
            if ticket_context:
                sections.append(f"# TICKET_CONTEXT\n{ticket_context}")

        # TASK
        if task:
            sections.append(f"# TASK\n{task}")

        return "\n\n".join(sections)

    def _load_ticket(self, ticket_id: str) -> str:
        """Load ticket context from task card."""
        from .paths import tasks_dir

        task_file = tasks_dir(self.project_dir) / ticket_id / "task.yaml"
        if task_file.exists():
            return task_file.read_text()
        return ""

    # ----- HATS-474 Phase 3: multi-turn API -----

    def session(
        self,
        role: str,
        *,
        model: str = "",
        isolation_mode: str = "discard",
        parent_session: str | None = None,
        tags: dict[str, str] | None = None,
        system_prompt_override: str | None = None,
        max_budget_usd: float | None = None,
        max_turns: int | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
    ):
        """Open a multi-turn sub-agent session as an async context manager.

        Usage::

            async with runner.session("maintainer", model="claude-haiku-4-5") as s:
                r1 = await s.send("Analyse auth.py")
                r2 = await s.send("Now refactor to use JWT")

        On entry: compose the role, init audit, open the worktree, spawn
        :class:`ClaudeSDKClient`. On exit: emit aggregated ``transcript
        .txt`` / ``reasoning.log`` and ``metrics.json`` (summed
        ``total_cost_usd``, total ``num_turns_total``, stable
        ``claude_session_id``, ``send_count``, last ``stop_reason``),
        then drop the per-session cache.

        ``max_budget_usd`` and ``max_turns`` (HATS-474 Phase 4) plumb
        through to ``ClaudeAgentOptions`` so e2e tests can bound CI cost
        even when the session spans many turns — the SDK enforces the
        cap and emits ``ResultMessage{subtype: "error_max_budget_usd"}``
        / ``"error_max_turns"`` when crossed.

        Claude-only — Gemini and future CLI-only providers don't have a
        multi-turn SDK channel. Use :meth:`run` for those.
        """
        provider_name = self.assembler.project_config.provider
        if provider_name != "claude":
            raise ValueError(
                f"SubAgentRunner.session() is Claude-only (got "
                f"provider={provider_name!r}). Use SubAgentRunner.run() "
                "for one-shot non-Claude sub-agents."
            )
        return self._session_impl(
            role=role,
            model=model,
            isolation_mode=isolation_mode,
            parent_session=parent_session,
            tags=tags,
            system_prompt_override=system_prompt_override,
            max_budget_usd=max_budget_usd,
            max_turns=max_turns,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )

    @asynccontextmanager
    async def _session_impl(
        self,
        *,
        role: str,
        model: str,
        isolation_mode: str,
        parent_session: str | None,
        tags: dict[str, str] | None,
        system_prompt_override: str | None,
        max_budget_usd: float | None,
        max_turns: int | None,
        permission_mode: str | None,
        allowed_tools: list[str] | None,
    ):
        """Implementation of :meth:`session` — wrapped by a thin sync
        validator so consumers get a clear ``ValueError`` on misuse
        (e.g. wrong provider) at call time rather than buried inside
        ``async with`` machinery.
        """
        from claude_agent_sdk import ClaudeSDKClient

        from .sdk_options import build_options
        from .subagent_session import SubAgentSession

        session = self.session_mgr.create_session(parent_session=parent_session)

        result = compose_for_role(self.assembler, role)
        # HATS-505 / HATS-452-class trap warning (multi-turn twin of
        # ``_run_attempt``): ``with_injection_override`` REPLACES
        # ``result.injections`` WHOLESALE. The same caveat applies as
        # in ``_run_attempt`` above — see that site for the full
        # explanation. ``SubAgentRunner.session()`` is the multi-turn
        # entry; no production caller currently passes an override
        # here, but the parameter survives for HATS-267 future use.
        if system_prompt_override is not None:
            result = result.with_injection_override(system_prompt_override)

        provider = get_provider(self.assembler.project_config.provider)

        meta_prompt = self._build_sdk_prompt_audit(
            result=result, task="", ticket_id="",
        )
        session.save_meta_prompt(meta_prompt)
        session.init_audit(
            role=role,
            provider=provider.name,
            model=model,
            composition=_composition_snapshot(self.assembler, role, result),
        )
        session.log_trace(
            TraceTag.SUB, f"Sub-agent session started: role={role}",
        )

        # HATS-474 review fix: hand the SDK only an ai-hats-specific
        # overlay; the SDK merges this on top of inherited os.environ at
        # spawn time, so the full host env doesn't sit on a long-lived
        # ClaudeAgentOptions object.
        sdk_env_overlay = {
            **session.get_env(),
            "AI_HATS_ROLE": role,
        }

        mode = IsolationMode(isolation_mode)
        session.log_trace(TraceTag.SUB, f"Isolation: {mode.value}")

        t0 = time.monotonic()
        sub: SubAgentSession | None = None
        yield_error: BaseException | None = None
        # HATS-535: latch work_dir so `_finalize_session_audit` can pass
        # it through to ``_finalize_sub_agent`` for ``finalize-subagent``
        # JSONL discovery — closing the multi-turn SubAgent audit parity
        # gap (single-turn `_run_attempt` callers already wire it).
        captured_work_dir: Path | None = None

        try:
            with WorktreeManager(
                self.project_dir, role, session.session_id, mode,
            ) as work_dir:
                captured_work_dir = work_dir
                session.log_trace(
                    TraceTag.SUB, f"Working directory: {work_dir}",
                )
                options = build_options(
                    result,
                    project_dir=self.project_dir,
                    session_id=session.session_id,
                    work_dir=work_dir,
                    model=model or "",
                    extra_env=sdk_env_overlay or None,
                    max_budget_usd=max_budget_usd,
                    max_turns=max_turns,
                    permission_mode=permission_mode,
                    allowed_tools=allowed_tools,
                )
                # HATS-474 review fix #4: also catch exceptions raised by
                # ClaudeSDKClient.__aexit__ (SDK shutdown failures) so
                # they reach _finalize_session_audit and land in
                # metrics.json.error. Without this wrapper an SDK-level
                # shutdown error would propagate past our local try
                # without populating yield_error, and the audit would
                # mis-classify the session as clean.
                try:
                    async with ClaudeSDKClient(options=options) as client:
                        sub = SubAgentSession(
                            client=client,
                            session=session,
                            role=role,
                            model=model,
                            isolation_mode=mode.value,
                        )
                        try:
                            yield sub
                        except BaseException as exc:
                            # Capture so the finally block can record it
                            # in metrics — then re-raise so the caller
                            # still sees the original exception.
                            yield_error = exc
                            raise
                except BaseException as exc:
                    # Either context entry / shutdown failed, or the
                    # inner block re-raised. Don't overwrite a
                    # yield_error already captured by the inner handler.
                    if yield_error is None:
                        yield_error = exc
                    raise
        finally:
            self._finalize_session_audit(
                session=session,
                role=role,
                model=model,
                mode=mode,
                sub=sub,
                yield_error=yield_error,
                tags=tags,
                duration_s=time.monotonic() - t0,
                work_dir=captured_work_dir,
            )
            _cleanup_session_cache(self.project_dir, session.session_id)

    def _finalize_session_audit(
        self,
        *,
        session: Session,
        role: str,
        model: str,
        mode: IsolationMode,
        sub,  # SubAgentSession | None — kept untyped to avoid import cycle
        yield_error: BaseException | None,
        tags: dict[str, str] | None,
        duration_s: float,
        work_dir: Path | None = None,
    ) -> None:
        """Emit the per-session ``transcript.txt`` / ``reasoning.log`` /
        ``metrics.json`` once a multi-turn session has finished.

        Centralises the conditional logic for: (a) clean session →
        aggregated stats from ``sub``, (b) yield raised but ``sub``
        existed → record the error alongside whatever turns ran, (c)
        setup failed before ``sub`` existed → minimal error finalize so
        ``session_dir`` is consistent regardless.
        """
        if sub is None:
            # Setup phase failed before SubAgentSession was created
            # (compose / worktree / SDK context entry).
            error_text = "session setup failed"
            if yield_error is not None:
                error_text = (
                    f"{type(yield_error).__name__}: {yield_error}"
                )
            _finalize_sub_agent(
                session,
                role=role,
                model=model,
                isolation_mode=mode.value,
                exit_code=SUBAGENT_EXIT_ERROR,
                error=error_text,
                tags=tags,
                duration_s=duration_s,
                work_dir=work_dir,
            )
            return

        exit_code = (
            SUBAGENT_EXIT_ERROR
            if (sub.is_error or yield_error is not None)
            else 0
        )
        error_text = sub.first_error
        if yield_error is not None and error_text is None:
            error_text = f"{type(yield_error).__name__}: {yield_error}"

        _finalize_sub_agent(
            session,
            role=role,
            model=model,
            isolation_mode=mode.value,
            exit_code=exit_code,
            stdout=sub.aggregated_transcript,
            stderr=sub.aggregated_reasoning,
            error=error_text,
            tags=tags,
            duration_s=duration_s,
            extra_metrics={
                "claude_session_id": sub.claude_session_id,
                "total_cost_usd": sub.total_cost_usd,
                "num_turns_total": sub.num_turns_total,
                "send_count": sub.send_count,
                "stop_reason": sub.last_stop_reason,
            },
            work_dir=work_dir,
        )
