"""HITL runner: PTY-wrapped interactive Claude session (WrapRunner).

Extracted from runtime.py (HATS-715); shared helpers live in runtime_common."""

from __future__ import annotations

import logging
import os
import select
import sys
import time
import uuid
from collections import deque
from pathlib import Path

from typing import TYPE_CHECKING

from .composition_payload import CompositionPayload
from .constants import ENV_ROLE, ENV_ROOT_PID, PROVIDER_CLAUDE

# HATS-649: the session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .pipeline.keys import PIPELINE_FINALIZE_HITL
from .pty_shutdown import bounded_proc_shutdown, emit_terminal_reset
from .runtime_common import (
    _TERM_RESET_PRELUDE,
    _ESCAPE_NOTICE,
    _scan_escape,
    _cleanup_session_cache,
    _print_session_start,
    _print_session_end,
    _finalize_session_basic,
    _run_finalize_hitl,
)
from .startup_notices import (
    StartupNotice,
    _countdown_hold,
    show_and_hold_startup_notices,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai_hats_observe import Session, SessionManager, SidecarTracer

logger = logging.getLogger(__name__)


# ----- HATS-833 session-start heal-note formatting -----

_SURFACE_LABEL = {"runtime": "runtime-hook", "wt": "wt-hook", "git": "git-hook"}
_KIND_PHRASE = {
    "missing": "materialized (was missing)",
    "content": "updated (content drift)",
    "wiring": "re-wired",
    "stale": "swept (no longer composed)",
}


def _hook_display_name(surface: str, name: str) -> str:
    """Drop the script extension for runtime/wt names (git ``name`` is the bare
    event)."""
    if surface in ("runtime", "wt") and "." in name:
        return name.rsplit(".", 1)[0]
    return name


def _format_hook_heal(changes) -> str:
    """One-glance heal note: one clause per changed hook, kinds on the same hook
    folded (``content`` + ``wiring`` → ``updated (content drift) + re-wired``)."""
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for c in changes:
        key = (c.surface, _hook_display_name(c.surface, c.name))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if c.kind not in grouped[key]:
            grouped[key].append(c.kind)
    clauses = []
    for surface, dname in order:
        phrases = " + ".join(_KIND_PHRASE.get(k, k) for k in grouped[(surface, dname)])
        clauses.append(f"{_SURFACE_LABEL.get(surface, surface)} {dname} {phrases}")
    return "managed hooks healed at start — " + "; ".join(clauses)


_COLLISION_HINTS = {
    "identical": "exact duplicate of the session plugin — safe to remove",
    "differs": "content differs from the ai-hats version — review: remove or rename",
}


def _collision_hint(c) -> str:
    """Post-HATS-931 the warn list is home-scope only (project collisions
    auto-heal; HATS-465 keeps home user-owned); the project branch is a
    defensive fallback for a collision the heal unexpectedly left behind."""
    if c.verdict == "managed":
        if c.scope == "home":
            return "ai-hats never manages user-level skills — remove manually if unwanted"
        return "stale ai-hats mirror — auto-heal did not run this start"
    return _COLLISION_HINTS[c.verdict]


def _format_skill_collisions(collisions) -> str:
    """HATS-901: name the skills Claude Code will register twice this session."""
    lines = [
        f"{len(collisions)} skill(s) will register twice this session "
        "(auto-discovery dir + ai-hats session plugin):"
    ]
    lines.extend(f"  {c.name} at {c.path} — {_collision_hint(c)}" for c in collisions)
    return "\n".join(lines)


def _format_mirror_heal(removed: list[str], trash_root) -> str:
    """HATS-907 heal note: self-serve recovery — names + trash destination."""
    listed = ", ".join(removed[:6]) + ("" if len(removed) <= 6 else f" (+{len(removed) - 6} more)")
    where = f" — recoverable in trash: {trash_root}" if trash_root else ""
    return (
        f"removed stale ai-hats skills mirror from .claude/skills "
        f"({len(removed)} skill(s): {listed}){where}"
    )


def _format_version_skew(changes) -> str:
    """Warn note when drift exists but the binary is behind upstream (req-7:
    name the unhealed drift rather than skip silently)."""
    seen: set[str] = set()
    uniq: list[str] = []
    for c in changes:
        label = f"{c.surface} {_hook_display_name(c.surface, c.name)}"
        if label not in seen:
            seen.add(label)
            uniq.append(label)
    listed = ", ".join(uniq[:6])
    more = "" if len(uniq) <= 6 else f" (+{len(uniq) - 6} more)"
    return (
        "managed hooks drifted but not healed — installed ai-hats is behind "
        "upstream. Run 'ai-hats self update'. Stale: " + listed + more + "."
    )


class WrapRunner:
    """PTY-proxied CLI wrapper for interactive sessions.

    HATS-865: a brick — receives the ready :class:`CompositionPayload` from
    the integrator compose seam and never touches the composition layer.
    HATS-867: the observe writer handles (``session_mgr``, ``tracer_factory``)
    are injected too — the runner never imports observe at runtime.
    """

    def __init__(
        self,
        project_dir: Path,
        payload: CompositionPayload,
        *,
        session_mgr: "SessionManager",
        tracer_factory: "Callable[[Session], SidecarTracer]",
    ) -> None:
        self.project_dir = project_dir
        self.payload = payload
        self.hooks = payload.hooks
        self.session_mgr = session_mgr
        self.tracer_factory = tracer_factory

    def _resync_managed_hooks(
        self, session: Session | None = None, result=None
    ) -> list[StartupNotice]:
        """Heal drift of ALL managed-hook surfaces at session start (HATS-833,
        generalizing HATS-593 layer B from git-only to runtime + wt + git).

        ``HooksManager.sync_hooks()`` is idempotent, drift-gated, skips a role-less
        project, and refuses to heal from a stale binary. Fail-open: a best-effort
        drift-heal must never block session start. The sole trigger is here —
        there is no ``ai-hats self sync-hooks`` command and no git-event hook
        anymore (HATS-833 Q2).

        Returns startup notices to surface (HATS-833 req-5): a single NOTE naming
        each healed hook + change kind on the heal path; a WARN on failure or when
        drift was detected but left unhealed under version-skew. Empty list on a
        clean in-sync start (silent). ``result`` reuses the session's composition
        to avoid a second compose.
        """
        try:
            res = self.hooks.sync_hooks(result)
            if session is not None:
                session.log_sys(f"managed-hook resync: {res.status}")
            notices: list[StartupNotice] = []
            if res.status == "synced" and res.changes:
                notices.append(StartupNotice("note", _format_hook_heal(res.changes)))
            if res.status == "version-skew":
                notices.append(StartupNotice("warn", _format_version_skew(res.changes)))
            # Genuine hooks warnings raised while healing (HATS-969) — through the hold.
            notices.extend(StartupNotice("warn", w) for w in res.warnings)
            return notices
        except Exception as exc:
            logger.warning("managed-hook resync at session start failed", exc_info=True)
            summary = f"managed-hook resync failed: {type(exc).__name__}: {exc}"
            if session is not None:
                session.log_sys(f"managed-hook resync FAILED — {summary}")
            return [StartupNotice("warn", summary)]

    def _payload_startup_notices(self) -> list[StartupNotice]:
        """Hooks warnings from the first-run compose seam (set_role materialize),
        carried on the payload → surfaced as WARN notices so they hit the read-hold
        instead of a bare pre-launch print (HATS-970)."""
        return [StartupNotice("warn", w) for w in self.payload.startup_warnings]

    def _check_skill_collisions(self, session: Session, result) -> list[StartupNotice]:
        """HATS-901: WARN when a composed skill will double-register this session;
        HATS-907: a marker-proven project-scope mirror is auto-healed instead.

        Fail-open — a broken auto-discovery dir must never block launch.
        """
        from .paths import session_cache_dir
        from .plugin_dir import duplicate_skill_registrations

        try:
            collisions = duplicate_skill_registrations(
                [s.name for s in result.skills],
                project_dir=self.project_dir,
                plugin_skills_root=session_cache_dir(self.project_dir, session.session_id)
                / "plugin"
                / "skills",
                home=Path.home(),
            )
        except OSError as exc:
            logger.debug("skill-collision check failed open: %s", exc)
            return []
        if not collisions:
            return []
        # HATS-931: every project-scope collision is heal-eligible — a name in
        # project .claude/skills that matches a composed skill is ai-hats-owned
        # (not a user-authoring surface), marker or not. Home scope → warn only.
        healable = [c for c in collisions if c.scope == "project"]
        rest = [c for c in collisions if c not in healable]
        notices: list[StartupNotice] = []
        if healable:
            notices.append(self._heal_managed_mirror(session, healable))
        if rest:
            notices.append(StartupNotice("warn", _format_skill_collisions(rest)))
        return notices

    def _heal_managed_mirror(self, session: Session, healable) -> StartupNotice:
        """HATS-907: sweep the marker-proven stale mirror pre-spawn. Gated on
        version-skew + hard-delete mode; fail-open. Rationale: task card."""
        from .plugin_dir import drop_legacy_skills_mirror
        from ai_hats_core.safe_delete import hard_delete_mode, session_root

        names = ", ".join(sorted({c.name for c in healable}))
        try:
            if self.hooks.binary_behind_source():
                return StartupNotice(
                    "warn",
                    f"stale ai-hats skills mirror ({names}) not auto-healed — "
                    "installed ai-hats is behind upstream. Run 'ai-hats self update'.",
                )
            if hard_delete_mode():
                return StartupNotice(
                    "warn",
                    f"stale ai-hats skills mirror ({names}) not auto-healed — "
                    "AI_HATS_TRASH_DIR=- would make the removal unrecoverable; "
                    "remove .claude/skills manually or unset it.",
                )
            removed = drop_legacy_skills_mirror(self.project_dir, names={c.name for c in healable})
            if not removed:
                return StartupNotice(
                    "warn",
                    f"stale ai-hats skills mirror ({names}) detected but the sweep "
                    "removed nothing — review .claude/skills manually.",
                )
            text = _format_mirror_heal(removed, session_root())
            session.log_sys(f"skills-mirror heal: {text}")
            return StartupNotice("note", text)
        except Exception as exc:
            logger.warning("skills-mirror heal at session start failed", exc_info=True)
            summary = f"skills-mirror heal failed: {type(exc).__name__}: {exc}"
            session.log_sys(f"skills-mirror heal FAILED — {summary}")
            return StartupNotice("warn", summary)

    def _lint_provider_settings(self, session: "Session") -> list[StartupNotice]:
        """HATS-1006: WARN per provider-reported settings pitfall — the surface's
        own warnings print post-spawn where the alt-screen clobbers them.
        Fail-open; the lint itself lives with the surface
        (``Provider.settings_lint_warnings``, docs/session-start-notices.md).
        """
        provider = self.payload.provider
        if provider is None:
            return []
        try:
            findings = provider.settings_lint_warnings(self.project_dir)
        except Exception as exc:
            logger.warning("provider settings lint at session start failed", exc_info=True)
            session.log_sys(f"provider settings lint FAILED — {type(exc).__name__}: {exc}")
            return []
        if findings:
            session.log_sys(f"provider settings lint: {len(findings)} finding(s)")
        return [StartupNotice("warn", text) for text in findings]

    def _lint_env_drift(self, session: "Session") -> list[StartupNotice]:
        """HATS-1013: WARN when the editable dev env is stale — uv freezes
        dist-info at sync time, so ``importlib.metadata`` / ``--version`` lie
        after a version bump until ``uv sync``. Fail-open; detection lives in
        :mod:`.env_drift` (gated to the dev checkout there).
        """
        try:
            from . import env_drift

            findings = env_drift.stale_dev_env_warnings()
        except Exception as exc:
            logger.warning("env-drift lint at session start failed", exc_info=True)
            session.log_sys(f"env-drift lint FAILED — {type(exc).__name__}: {exc}")
            return []
        if findings:
            session.log_sys(f"env-drift lint: {len(findings)} finding(s)")
        return [StartupNotice("warn", text) for text in findings]

    def _hold_before_launch(self, startup_notices: list[StartupNotice]) -> None:
        """Show any startup notices and hold before the wrapped TUI spawns
        (HATS-825, HATS-833). Delegates the "notices ⇒ show and wait" policy to
        :func:`show_and_hold_startup_notices`; supplies a Ctrl-C-aware countdown
        as the wait. Ctrl-C propagates — ``run()``'s handler turns it into a clean
        exit (130) that finalizes the session and never spawns the CLI.
        """
        try:
            show_and_hold_startup_notices(
                startup_notices,
                is_tty=sys.stdin.isatty(),
                sleep=lambda d: self._sleep_countdown(d, announce=bool(startup_notices)),
            )
        except KeyboardInterrupt:
            print("\n\033[1;31m  launch aborted\033[0m")
            raise

    @staticmethod
    def _poll_enter(timeout: float) -> bool:
        """Block up to ``timeout`` seconds for the user to press Enter (HATS-847).

        On a TTY, ``select`` waits for stdin to become readable; the terminal is
        still in cooked mode here (the PTY has not spawned), so it reports ready
        only on a complete line — exactly an Enter press. The line is drained so
        the keystroke does not leak into the wrapped TUI's first prompt. Returns
        ``True`` when Enter arrived (skip the wait), ``False`` on timeout. Off a
        TTY there is nothing to read, so it just sleeps the budget and never
        skips — but that path is unreachable in practice (a non-tty start holds
        for 0 s; see ``_startup_hold_seconds``).
        """
        if not sys.stdin.isatty():
            time.sleep(timeout)
            return False
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return False
        sys.stdin.readline()  # drain the Enter line so it doesn't reach the TUI
        return True

    @staticmethod
    def _sleep_countdown(seconds: float, *, announce: bool) -> None:
        """Sleep ``seconds``; when ``announce``, show a live 1-Hz countdown that
        Enter cuts short (HATS-847) — Ctrl-C still aborts via the SIGINT that
        propagates out of the wait."""
        whole = int(seconds)
        if not announce or whole <= 0:
            time.sleep(seconds)
            return

        def render(remaining: int) -> None:
            sys.stdout.write(
                f"\r\033[2m  starting in {remaining}s — Enter to skip · Ctrl-C to abort \033[0m"
            )
            sys.stdout.flush()

        _countdown_hold(whole, render=render, poll_skip=WrapRunner._poll_enter)
        sys.stdout.write("\r\033[2K")  # wipe the countdown line before the TUI
        sys.stdout.flush()

    def run(
        self,
        extra_args: list[str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> tuple[int, Session]:
        """Launch a wrapped CLI session with PTY proxying.

        Returns (exit_code, session) so callers that need the session
        artefacts (transcript_path, audit, etc.) get them directly.

        HATS-452 (П2 in ADR-0005). ``WrapRunner`` is the **HITL** runner —
        a human is at the keyboard and the role's full composition reaches
        the agent through ``build_session_prompt``. It deliberately has
        **no** ``system_prompt_override`` channel: prompt injection in HITL
        is meaningless and the previously-exposed Optional override was the
        literal trap that caused HATS-452. Callers needing an explicit
        prompt use ``SubAgentRunner`` (Automate path).

        HATS-865: role resolution, the first-run ``set_role`` side effect,
        and the ONE composition all happened at the integrator compose seam
        (``composition_seam.build_composition_payload``) — this runner only
        delivers ``self.payload``.
        """
        payload = self.payload
        provider = payload.provider
        provider_name = provider.name
        active_role = payload.effective_role

        # HATS-649 (R2): the session-cache sweep + incomplete-version sweep +
        # orphan-version reclaim + this run's liveness-ref write now run inside
        # `create_session` (EnvironmentRecovery), the universal seam both
        # WrapRunner and SubAgentRunner traverse — so the previously
        # WrapRunner-only inline sweeps are gone from here. Create the session
        # before build_session_prompt so we can key the per-session cache dir on
        # session.session_id (HATS-294).
        session = self.session_mgr.create_session()

        # HATS-452 (П2): no override channel on WrapRunner — the payload's
        # composition flows straight into ``build_session_prompt``.
        result = payload.result
        session_args, session_env, meta_prompt = provider.build_session_prompt(
            self.project_dir,
            result,
            session.session_id,
        )
        session.init_audit(
            role=active_role,
            provider=provider_name,
            composition=payload.snapshot,
        )
        # HATS-523: persist materialized system prompt to
        # <session_dir>/meta_prompt.txt — symmetric with SubAgentRunner
        # (runtime.py ~1091). Exact bytes that reached the provider (post
        # HATS-380 placeholder expansion). Saved before hooks / _pty_spawn so
        # the artefact survives early failures.
        session.save_meta_prompt(meta_prompt)
        session.log_sys(f"Session started: role={active_role}")

        # Log CLI restart gap from previous session (helps judge distinguish
        # restarts from provider stalls).
        self._log_restart_gap(session)

        # Build environment
        env = {
            **os.environ,
            **session.get_env(),
            **provider.get_env(session.session_dir, self.project_dir),
            **session_env,
            ENV_ROLE: active_role,
            ENV_ROOT_PID: str(os.getpid()),  # HATS-955: ownership liveness anchor
        }

        # HATS-833: fail-open session-start drift net for all managed-hook
        # surfaces; reuses the composition above and returns startup notices.
        startup_notices: list[StartupNotice] = []
        startup_notices.extend(self._resync_managed_hooks(session, result))
        startup_notices.extend(self._check_skill_collisions(session, result))
        startup_notices.extend(self._payload_startup_notices())
        startup_notices.extend(self._lint_provider_settings(session))
        startup_notices.extend(self._lint_env_drift(session))

        # Build CLI command with session ID for JSONL linkage
        claude_session_id = str(uuid.uuid4())
        cmd = provider.get_cli_command(extra_args)
        cmd.extend(session_args)
        # Don't inject --session-id when the user is resuming/continuing
        # an existing session — it already has its own id, and Claude CLI
        # rejects --session-id + --resume without --fork-session.
        _resuming = extra_args and any(f in extra_args for f in ("--resume", "--continue", "-c"))
        if provider_name == PROVIDER_CLAUDE and not _resuming:
            cmd += ["--session-id", claude_session_id]
        session.log_sys(f"Launching: {' '.join(cmd)}")
        session.append_audit(f"Launched {provider_name} CLI")

        # HATS-566: eager-load finalize pipeline NOW so its YAML is
        # parsed against the step registry that's currently in memory.
        # If we deferred this to the `finally` block (where
        # ``_run_finalize_hitl`` lives), a long-running session that
        # straddles a working-tree update (editable install + ``git
        # pull`` mid-session) would read the *new* YAML against the
        # *old* registry — see the StepRegistryError observed in
        # session 20260527-085647-1 after the HATS-530 merge landed
        # while the wrap was still alive. The cache in
        # ``loader._CORE_PIPELINE_CACHE`` makes the later
        # ``load_core_pipeline`` call inside ``_run_finalize_hitl`` a
        # no-op lookup.
        try:
            from .pipeline.loader import load_core_pipeline

            load_core_pipeline(PIPELINE_FINALIZE_HITL)
        except Exception as exc:
            logger.warning("finalize-hitl preload failed", exc_info=True)
            summary = f"finalize-hitl preload failed: {type(exc).__name__}: {exc}"
            session.log_sys(f"finalize-hitl preload FAILED — {summary}")
            startup_notices.append(StartupNotice("warn", summary))

        from . import __version__

        _print_session_start(
            active_role,
            provider_name,
            session.session_id,
            version=__version__,
            channel=payload.channel,
        )

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
        tracer = self.tracer_factory(session)
        exit_code = 130  # canonical SIGINT default if _pty_spawn raises pre-assignment
        try:
            # HATS-825: brief pre-launch hold so the start banner + any
            # fail-open startup warning are readable before the TUI clobbers
            # them. Ctrl-C here aborts the launch (caught below → exit 130).
            self._hold_before_launch(startup_notices)
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
                        exit_code=exit_code,
                        static_cost_analyzer=payload.static_cost_analyzer,
                        session_factory=payload.session_factory,
                        audit_writer_factory=payload.audit_writer_factory,
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
                session.log_sys(f"CLI restart gap: {gap_str}")
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

        # HATS-713: pass the per-session env to the child via PtyProcess.spawn's
        # env= rather than mutating os.environ. Mutating os.environ permanently
        # polluted the PARENT process with per-session keys (AI_HATS_SESSION_ID,
        # AI_HATS_ROLE, provider vars) that then leaked into the finalize
        # pipeline, SESSION_END hooks, and any later WrapRunner.run in the same
        # process. The {**os.environ, **env} merge keeps os.environ as the base
        # (callers may pass a partial env), without writing back to it.
        child_env = {**os.environ, **env}

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
            proc = PtyProcess.spawn(cmd, dimensions=(rows, cols), env=child_env)
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
        # HATS-679: parent escape-hatch state — timestamps of consecutive
        # Ctrl-C presses and the force-exit flag (checked after the finally so
        # a hatch-triggered exit returns 130).
        escape_presses: deque[float] = deque()
        forced_exit = False
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
                                tracer.session.log_sys(
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
                    # HATS-679: count consecutive Ctrl-C. Forward everything up
                    # to the triggering byte (so the 1st/2nd still reach the
                    # child); on the 3rd within the window, withhold it, print
                    # the notice, and break out to the bounded shutdown.
                    forward, triggered = _scan_escape(data, escape_presses, time.monotonic())
                    # Latch forced_exit BEFORE any write: if the forward write
                    # below raises OSError on the very chunk that trips the
                    # hatch, breaking out must still return 130 — never let the
                    # bounded-shutdown SIGKILL surface as 137/124 instead (the
                    # exact mis-report this hatch exists to prevent).
                    if triggered:
                        forced_exit = True
                    if forward:
                        try:
                            os.write(master_fd, forward)
                        except OSError:
                            break
                    if triggered:
                        try:
                            os.write(stdout_fd, _ESCAPE_NOTICE)
                        except OSError:
                            pass
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

        # HATS-679: the parent escape-hatch fired (triple Ctrl-C against a
        # wedged child). bounded_proc_shutdown (above) already killed the child,
        # which would otherwise surface as signalstatus=SIGKILL → 137; check
        # forced_exit FIRST so a hatch-triggered exit is the canonical 130
        # (128 + SIGINT), not the shutdown's kill signal.
        if forced_exit:
            return 130
        if proc.exitstatus is not None:
            return int(proc.exitstatus)
        if proc.signalstatus is not None:
            return 128 + int(proc.signalstatus)
        # HATS-411: bounded_proc_shutdown could not confirm clean exit
        # (child stuck in `?Es` — WNOHANG reap returned (0, 0)). Surface
        # this as 124 (GNU coreutils `timeout` convention, also used by
        # SUBAGENT_EXIT_TIMEOUT) instead of silently returning success.
        return 124
