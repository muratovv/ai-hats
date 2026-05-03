"""Runtime — PTY wrapping, hooks execution, sub-agent launch."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .assembler import Assembler
from .models import LifecycleEvent
from .observe import AuditWriter, Session, SessionManager, SidecarTracer, TraceTag
from .providers import get_provider
from .worktree import IsolationMode, WorktreeManager

logger = logging.getLogger(__name__)

# Sub-agent subprocess wall-clock limit. Exceeding this raises TimeoutExpired,
# which is handled by graceful finalize (partial transcript + exit_code=124).
SUBAGENT_SUBPROCESS_TIMEOUT_S = 600

# Exit code conventions for early termination. 124 matches GNU coreutils `timeout`.
SUBAGENT_EXIT_TIMEOUT = 124
SUBAGENT_EXIT_ERROR = 1

# HATS-215: emitted on stdout before each PTY child spawn to neutralise
# DEC private modes that a prior TUI session may have leaked. Each sequence
# is idempotent on a clean terminal.
#   \x1b[<u       — pop kitty-keyboard enhancement stack (root cause of
#                   "Enter inserts newline" between consecutive sessions
#                   under ghostty + kitty-keyboard protocol)
#   \x1b[?2004l   — disable bracketed paste
#   \x1b[?1l      — exit application cursor mode (DECCKM off)
#   \x1b[?25h     — show cursor (in case prior TUI hid it and crashed)
_TERM_RESET_PRELUDE = "\x1b[<u\x1b[?2004l\x1b[?1l\x1b[?25h"


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
) -> None:
    """Save transcripts and finalize audit with structured metrics.

    Called from every sub-agent terminal path (success, timeout, error) so
    session_dir is always consistently closed: transcript.txt + reasoning.log
    written if we have any output, metrics.json written with exit_code and
    optional timed_out/error/tags/duration_s fields. Provider-agnostic —
    behaves identically for claude and gemini.
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

    session.finalize_audit(metrics)


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


def _print_session_start(role: str, provider: str, session_id: str) -> None:
    role_info = f"\033[1;36m{role or 'none'}\033[0m"
    provider_info = f"\033[1;35m{provider}\033[0m"
    print(f"\n[*] Role: {role_info} | Provider: {provider_info} | Session: {session_id}\n")


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

        rem = retro.get("reminder")
        if rem:
            # Yellow lead-line + cyan command — visually distinct from the green ✨ banner.
            print(
                f"\033[33m  Reflect the project through {rem['count']} sessions:\033[0m"
            )
            print(f"     \033[36m{rem['command']}\033[0m")
    print(f"  {_format_tokens(session)}")
    print(f"  📂 {session.session_dir}")
    print("━" * 52 + "\n")


def _finalize_session(
    session: "Session",
    *,
    exit_code: int,
    active_role: str | None,
    provider_name: str,
    claude_session_id: str,
    project_dir: Path,
    env: dict[str, str],
    hooks_runner: "HooksRunner",
    tracer: "SidecarTracer",
    tags: dict[str, str] | None = None,
) -> None:
    """Run all post-pty cleanup steps with per-step isolation. ALWAYS
    calls _print_session_end at the end, even when individual steps fail
    or are interrupted by SIGINT mid-cleanup. HATS-086.

    Each step catches both ``Exception`` and ``KeyboardInterrupt`` so a
    second Ctrl+C cannot kill cleanup partway. Errors are logged at WARN
    level. The summary print is the last step and is itself wrapped — if
    even that fails, we fall back to a bare-bones session-id line so the
    user never loses the id.
    """
    trace_stats: dict | None = None
    retro_decision: dict | None = None

    try:
        try:
            tracer.flush_response()
        except (Exception, KeyboardInterrupt):
            logger.warning("trace flush failed", exc_info=True)

        try:
            session.log_trace(TraceTag.SYS, f"Session ended: exit_code={exit_code}")
            session.append_audit(f"Session ended with code {exit_code}")
        except (Exception, KeyboardInterrupt):
            logger.warning("session trace/audit append failed", exc_info=True)

        # Finalize metrics and build enriched audit BEFORE hooks, so
        # session_end hooks can read metrics.json (e.g. auto-retro).
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

        try:
            jsonl_path = _claude_jsonl_path(project_dir, claude_session_id)
            AuditWriter().build(session, jsonl_path=jsonl_path)
        except (Exception, KeyboardInterrupt):
            logger.warning("audit writer failed", exc_info=True)

        # Smoke-test: non-error session should have turns after enrichment
        try:
            if exit_code == 0 and session.metrics_path.exists():
                metrics = json.loads(session.metrics_path.read_text())
                if metrics.get("turns", 0) == 0:
                    logger.warning(
                        "session %s: exit_code=0 but turns=0 — metrics may be incomplete",
                        session.session_id,
                    )
        except (Exception, KeyboardInterrupt):
            pass

        # Pre-compute retro decision synchronously BEFORE hooks fire.
        # Why: runtime must surface the outcome (path or skip reason) in
        # the banner even if the hook crashes or the harness is SIGKILLed.
        # The hook re-evaluates the same pure function independently.
        try:
            from .retro.auto_retro import make_decision, write_retro_log

            retro_decision = make_decision(project_dir, session.session_id)
            write_retro_log(
                project_dir, session.session_id,
                "runtime", "decision",
                f"{retro_decision['action']}: {retro_decision['reason']}",
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("retro decision/log failed", exc_info=True)

        # Run session_end hooks AFTER metrics.json and enriched audit
        # are written, so hooks (e.g. auto-retro) can read them.
        try:
            hook_results = hooks_runner.run(LifecycleEvent.SESSION_END, env=env)
            for hr in hook_results:
                if hr.get("stderr"):
                    print(hr["stderr"], end="", file=sys.stderr)
        except (Exception, KeyboardInterrupt):
            logger.warning("session_end hook failed", exc_info=True)
    finally:
        # The summary print is the only thing that surfaces the session id
        # to the user. It MUST run, even on second SIGINT, even if every
        # step above failed.
        try:
            _print_session_end(session, trace_stats=trace_stats, retro=retro_decision)
        except (Exception, KeyboardInterrupt):
            logger.warning("session-end print failed", exc_info=True)
            try:
                print(f"\n✨ Session {session.session_id} complete!")
            except (BrokenPipeError, OSError):
                pass


class WrapRunner:
    """PTY-proxied CLI wrapper for interactive sessions."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def run(
        self,
        provider_name: str,
        role_override: str | None = None,
        extra_args: list[str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Launch a wrapped CLI session with PTY proxying."""
        # Resolve provider
        provider = get_provider(provider_name)

        # Determine which role to use
        cfg = self.assembler.project_config
        effective_role = role_override or cfg.active_role or cfg.default_role

        # Role override uses shadow prompt (temp file) — never modifies project files.
        # Permanent assembly only when no override.
        override_args: list[str] = []
        override_env: dict[str, str] = {}

        if role_override and cfg.active_role:
            # Shadow override: compose to temp file, pass via CLI flags
            result = self.assembler.composer.compose(
                effective_role, overlay=self.assembler._get_overlay(effective_role),
            )
            override_args, override_env = provider.build_override(
                self.project_dir, result, self.session_mgr,
            )
        elif effective_role:
            needs_assembly = (
                not cfg.active_role  # no role set yet
                or cfg.provider != provider_name  # provider mismatch
            )
            if needs_assembly:
                self.assembler.set_role(effective_role, provider_name)

        # Reload after potential set_role
        cfg = self.assembler.project_config
        active_role = role_override or cfg.active_role

        # Create session
        session = self.session_mgr.create_session()
        session.init_audit(
            role=active_role,
            provider=provider_name,
        )
        session.log_trace(TraceTag.SYS, f"Session started: role={active_role}")

        # Log CLI restart gap from previous session (helps judge distinguish
        # restarts from provider stalls).
        self._log_restart_gap(session)

        # Build environment
        env = {
            **os.environ,
            **session.get_env(),
            **provider.get_env(session.session_dir, self.project_dir),
            **override_env,
            "AI_HATS_ROLE": active_role,
        }

        # Run hooks: session_start
        hooks_runner = HooksRunner(
            self.project_dir / ".agent" / "hooks",
            self.project_dir,
        )
        hooks_runner.run(LifecycleEvent.SESSION_START, env=env)
        session.log_trace(TraceTag.SYS, "hooks.session_start completed")

        # Build CLI command with session ID for JSONL linkage
        claude_session_id = str(uuid.uuid4())
        cmd = provider.get_cli_command(extra_args)
        cmd.extend(override_args)
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
        # routes through _finalize_session in the finally block, ensuring
        # the session-end summary (with the all-important session id) is
        # always printed.
        tracer = SidecarTracer(session)
        exit_code = 130  # canonical SIGINT default if _pty_spawn raises pre-assignment
        try:
            exit_code = self._pty_spawn(cmd, env, tracer)
        except KeyboardInterrupt:
            exit_code = 130
        finally:
            _finalize_session(
                session,
                exit_code=exit_code,
                active_role=active_role,
                provider_name=provider_name,
                claude_session_id=claude_session_id,
                project_dir=self.project_dir,
                env=env,
                hooks_runner=hooks_runner,
                tracer=tracer,
                tags=tags,
            )

        return exit_code

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
            if proc.isalive():
                try:
                    proc.terminate(force=True)
                except Exception:
                    pass
            try:
                proc.wait()
            except Exception:
                pass

        if proc.exitstatus is not None:
            return int(proc.exitstatus)
        if proc.signalstatus is not None:
            return 128 + int(proc.signalstatus)
        return 0


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
    ) -> Session:
        """Execute a sub-agent in isolation."""
        # Create sub-session
        session = self.session_mgr.create_session(parent_session=parent_session)

        # Compose the role
        result = self.assembler.composer.compose(
            role_name, overlay=self.assembler._get_overlay(role_name),
        )
        provider_name = self.assembler.project_config.provider
        provider = get_provider(provider_name)

        # Build meta-prompt
        meta_prompt = self._build_meta_prompt(
            result=result,
            provider=provider,
            task=task,
            ticket_id=ticket_id,
        )
        session.save_meta_prompt(meta_prompt)
        session.init_audit(role=role_name, provider=provider.name, model=model)
        session.log_trace(TraceTag.SUB, f"Sub-agent started: role={role_name}")

        # For now, execute via CLI subprocess (SDK integration is provider-specific)
        env = {
            **os.environ,
            **session.get_env(),
            "AI_HATS_ROLE": role_name,
        }

        cmd = provider.get_cli_command()
        # Add prompt via stdin for non-interactive execution
        session.log_trace(TraceTag.SUB, f"Executing: {' '.join(cmd)}")

        mode = IsolationMode(isolation_mode)
        session.log_trace(TraceTag.SUB, f"Isolation: {mode.value}")

        with WorktreeManager(self.project_dir, role_name, session.session_id, mode) as work_dir:
            session.log_trace(TraceTag.SUB, f"Working directory: {work_dir}")
            t0 = time.monotonic()
            try:
                full_cmd = provider.get_run_command(cmd, meta_prompt)
                proc = subprocess.run(
                    full_cmd,
                    cwd=str(work_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=SUBAGENT_SUBPROCESS_TIMEOUT_S,
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
                )

            except subprocess.TimeoutExpired as exc:
                session.log_trace(
                    TraceTag.SYS,
                    f"Sub-agent timed out after {SUBAGENT_SUBPROCESS_TIMEOUT_S}s",
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
                )
            except Exception as e:
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
                )

        return session

    def _build_meta_prompt(self, result, provider, task: str, ticket_id: str) -> str:
        """Build the meta-prompt for sub-agent execution."""
        sections = []

        # SYSTEM_ROLE
        sections.append(f"# SYSTEM_ROLE\n{result.merged_injection}")

        # PROJECT_STATE
        state_md = self.project_dir / ".agent" / "STATE.md"
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
        task_file = self.project_dir / ".agent" / "backlog" / "tasks" / ticket_id / "task.yaml"
        if task_file.exists():
            return task_file.read_text()
        return ""
