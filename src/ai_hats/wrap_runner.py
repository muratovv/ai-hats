"""HITL runner: PTY-wrapped interactive Claude session (WrapRunner).

Extracted from runtime.py (HATS-715); shared helpers live in runtime_common."""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from collections import deque
from pathlib import Path

from typing import TYPE_CHECKING

from .assembler import Assembler

# HATS-649: the session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .materialize import compose_for_role
from .observe import Session, SessionManager, SidecarTracer, TraceTag
from .providers import get_provider
from .pty_shutdown import bounded_proc_shutdown, emit_terminal_reset
from .runtime_common import (
    _TERM_RESET_PRELUDE,
    _ESCAPE_NOTICE,
    _scan_escape,
    _cleanup_session_cache,
    _print_session_start,
    _composition_snapshot,
    _print_session_end,
    _finalize_session_basic,
    _run_finalize_hitl,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class WrapRunner:
    """PTY-proxied CLI wrapper for interactive sessions."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def _resync_git_hooks(self, session: Session | None = None) -> None:
        """Re-heal git-hook drift at session start (HATS-593 layer B).

        Re-homed in HATS-707 from the maintainer's
        ``session_start: [ai-hats self sync-hooks]`` lifecycle-hook
        declaration. That declaration never executed — the ``hooks:``
        composition channel had zero runtime consumers (HooksRunner scanned
        a tree empty since HATS-314), so the in-session drift net was dead.
        ``Assembler.sync_hooks()`` is idempotent, skips a non-git / role-less
        project, and refuses on a stale binary. Fail-open: a best-effort
        drift-heal must never block session start.
        """
        try:
            res = self.assembler.sync_hooks()
            if session is not None:
                session.log_trace(TraceTag.SYS, f"git-hook resync: {res.status}")
        except Exception:
            logger.warning("git-hook resync at session start failed", exc_info=True)

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

        # HATS-649 (R2): the session-cache sweep + incomplete-version sweep +
        # orphan-version reclaim + this run's liveness-ref write now run inside
        # `create_session` (EnvironmentRecovery), the universal seam both
        # WrapRunner and SubAgentRunner traverse — so the previously
        # WrapRunner-only inline sweeps are gone from here. Create the session
        # before build_session_prompt so we can key the per-session cache dir on
        # session.session_id (HATS-294).
        session = self.session_mgr.create_session()

        # HATS-456: single derivation point for "compose for role X".
        # HATS-452 (П2): no override channel on WrapRunner — the composition
        # produced here flows straight into ``build_session_prompt``.
        result = compose_for_role(self.assembler, effective_role)
        session_args, session_env, meta_prompt = provider.build_session_prompt(
            self.project_dir,
            result,
            session.session_id,
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

        # HATS-707: in-session git-hook drift net (HATS-593 layer B), re-homed
        # from the dead lifecycle ``hooks:`` channel to a direct sync_hooks()
        # call. Fail-open; idempotent no-op when hooks are already in sync.
        self._resync_git_hooks(session)

        # Build CLI command with session ID for JSONL linkage
        claude_session_id = str(uuid.uuid4())
        cmd = provider.get_cli_command(extra_args)
        cmd.extend(session_args)
        # Don't inject --session-id when the user is resuming/continuing
        # an existing session — it already has its own id, and Claude CLI
        # rejects --session-id + --resume without --fork-session.
        _resuming = extra_args and any(f in extra_args for f in ("--resume", "--continue", "-c"))
        if provider_name == "claude" and not _resuming:
            cmd += ["--session-id", claude_session_id]
        session.log_trace(TraceTag.SYS, f"Launching: {' '.join(cmd)}")
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

            load_core_pipeline("finalize-hitl")
        except Exception:
            logger.warning("finalize-hitl preload failed", exc_info=True)

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
