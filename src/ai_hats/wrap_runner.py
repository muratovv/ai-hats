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
from .consent_wrapper import materialize_consent_wrappers

# HATS-649: the session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .pipeline import warm
from .pipeline_catalog import FINALIZE_HITL
from .pty_shutdown import bounded_proc_shutdown, emit_terminal_reset
from .pty_tap import NullPtyTap
from .check_snapshot import describe_checks, legacy_launch_notices, surface_skew_notice
from .session_identity import SessionIdentity
from .session_artifacts import (
    BuiltArtifacts,
    RunMode,
    assemble_launch_command,
    assemble_launch_env,
    consumed_session_id,
)
from .session_report import SessionReport
from .startup_checks import run_startup_checks
from .runtime_common import (
    _TERM_RESET_PRELUDE,
    _ESCAPE_NOTICE,
    _scan_escape,
    _claim_session_cache,
    _claim_surface_child,
    _cleanup_session_cache,
    _print_session_start,
    _print_session_end,
    _finalize_session_basic,
    _flag_sensor_error,
    _run_finalize_hitl,
    FinalizeAborted,
    sigint_shield,
)
from .startup_notices import (
    StartupNotice,
    _countdown_hold,
    _startup_hold_seconds,
    save_session_diagnostics,
    show_and_hold_startup_notices,
    strip_ansi_and_control_codes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai_hats_core import CompositionResult
    from ai_hats_observe import Session, SessionManager, SidecarTracer

    from .pty_tap import PtyTapFactory

logger = logging.getLogger(__name__)


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


def _broken_hook_refs_text(refs, *, project_dir: Path, ours: bool) -> str:
    """One instruction for the refs of one ownership (HATS-1522).

    This text is the only instruction anyone gets — nobody reads the source
    after it — so it answers three questions on its own: what broke and how
    badly, what to run, and what that run changes beyond the repair. It names
    `self init`, not `self update`: the latter reinstalls the harness from
    GitHub, which on an editable install is somebody's working checkout.
    """
    files = ", ".join(sorted({r.settings_file for r in refs}))
    head = (
        f"1 hook in {files} points at a file that is gone"
        if len(refs) == 1
        else f"{len(refs)} hooks in {files} point at files that are gone"
    )
    them = "these entries" if len(refs) > 1 else "this entry"
    lines = [f"{head} — the harness reports an error on every matching tool call:"]
    lines += [f"    {r.event}  →  {r.command}" for r in refs]
    if ours:
        lines += [
            f"    ai-hats wrote {them}, so it can clean up for you:",
            "",
            f"    Fix: cd {project_dir} && ai-hats self init --no-wizard",
            "",
            "    That command also applies any ai-hats migration this project has",
            "    not seen yet, so expect other files under it to change.",
        ]
    else:
        it = "them" if len(refs) > 1 else "it"
        missing = "files" if len(refs) > 1 else "file"
        lines += [
            f"    ai-hats did not write {them} and will not touch {it} —",
            f"    delete {it} yourself, or put the missing {missing} back.",
        ]
    return "\n".join(lines)


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
        """Retired per HATS-1480 / D5: all managed hook surfaces are materialized
        at init/session-build time; no session-start drift net remains."""
        return []

    def _payload_startup_notices(self) -> list[StartupNotice]:
        """What the compose seam carried, surfaced so it hits the read-hold instead
        of a bare pre-launch print the alternate screen buffer eats.

        Two producers, one hold: hooks warnings arrive as bare strings and are
        warnings by construction (HATS-970); composition diagnostics arrive typed
        and keep the level their producer chose (HATS-1753).
        """
        return [
            *(StartupNotice("warn", w) for w in self.payload.startup_warnings),
            *(StartupNotice(diag.level.value, diag.render()) for diag in self.payload.diagnostics),
        ]

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

    def _check_broken_hook_refs(self, session: "Session") -> list[StartupNotice]:
        """HATS-1509: WARN per settings hook ref pointing at a missing script —
        the harness prints 'No such file or directory' on every matching call,
        with no hint that an ``ai-hats:``-tagged one is ours to reclaim. Reports
        only; the install-time sweep stays the sole deleter (HATS-905). Fail-open.
        """
        try:
            from . import migration_assert

            broken = migration_assert.find_broken_hook_refs(
                self.project_dir, targets=migration_assert.SESSION_SCAN_TARGETS
            )
        except Exception as exc:
            logger.warning("broken-hook-ref scan at session start failed", exc_info=True)
            session.log_sys(f"broken-hook-ref scan FAILED — {type(exc).__name__}: {exc}")
            return []
        if broken:
            session.log_sys(f"broken hook refs: {len(broken)} finding(s)")
        # One instruction per ownership, not per finding: the remedy differs by
        # ownership and only by that (HATS-1522).
        notices = []
        for ours in (True, False):
            group = [ref for ref in broken if bool(ref.managed) is ours]
            if group:
                notices.append(
                    StartupNotice(
                        "warn",
                        _broken_hook_refs_text(group, project_dir=self.project_dir, ours=ours),
                    )
                )
        return notices

    def _sweep_consent_store(self, session: "Session") -> None:
        """HATS-1682: clear stale consent tickets once per session.

        Nobody else can. The store's own sweeps ride `mint` and `consume`, so a
        refused ticket sits until the next gated transition — and the agent may
        not `rm` under it, by design. Session start is the engine's turn.
        """
        from ai_hats_library.hooks import consent_ticket

        try:
            dropped = consent_ticket.sweep(self.project_dir)
        except Exception as exc:
            logger.warning("consent-store sweep at session start failed", exc_info=True)
            session.log_sys(f"consent-store sweep FAILED — {type(exc).__name__}: {exc}")
            return
        if dropped:
            session.log_sys(f"consent store: {dropped} stale ticket(s) swept")

    def _check_skill_script_collisions(
        self, session: "Session", result: "CompositionResult"
    ) -> list[StartupNotice]:
        """HATS-1114: WARN when composed skills contain script filename collisions.
        Returns startup warnings so the hold banner surfaces them to the human before
        the TUI launch.
        """
        try:
            from .skills_dir import find_skill_script_collisions

            collisions = find_skill_script_collisions(result.skills)
        except Exception as exc:
            logger.warning("skill script collision check at session start failed", exc_info=True)
            session.log_sys(f"skill collision check FAILED — {type(exc).__name__}: {exc}")
            return []
        if collisions:
            session.log_sys(f"skill script collisions: {len(collisions)} finding(s)")
        return [StartupNotice("warn", text) for text in collisions]

    def _hold_before_launch(
        self,
        startup_notices: list[StartupNotice],
        env: dict[str, str] | None = None,
    ) -> None:
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
                env=env,
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
        pty_tap_factory: PtyTapFactory | None = None,
    ) -> tuple[int, Session]:
        """Launch a wrapped CLI session with PTY proxying.

        Returns (exit_code, session) so callers that need the session
        artefacts (transcript_path, audit, etc.) get them directly.

        HATS-452 (D2 in ADR-0005). ``WrapRunner`` is the **HITL** runner —
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

        # HATS-452 (D2): no override channel on WrapRunner — the payload's
        # composition flows straight into the builder.
        builder_notices: list[StartupNotice] = []
        artifacts = BuiltArtifacts()
        with provider.execution_context(self.project_dir):
            result = payload.result
            if provider.handles_artifact_categories():
                artifacts = provider.build_session_artifacts(
                    self.project_dir,
                    result,
                    session.session_id,
                    run_mode=RunMode.HITL,
                    policy=payload.policy,
                    artifacts=artifacts,
                )
                session_args = artifacts.cli_args
                session_env = artifacts.extra_env
                meta_prompt = artifacts.full_content or ""
                # HATS-1540: a surface older than `session_skills_root` handles
                # this seam fine and still cannot root a bound check. Said here,
                # at launch, not at the first refused transition.
                skew = surface_skew_notice(provider_name, provider, self.project_dir, result)
                if skew:
                    builder_notices.append(StartupNotice("warn", skew))
            else:
                # HATS-1207 R4 / HATS-1241: the legacy entry point predates both
                # SessionPolicy and the check snapshot — loudly, not in silence.
                session_args, session_env, meta_prompt = provider.build_session_prompt(
                    self.project_dir, result, session.session_id
                )
                artifacts.extra_env.update(session_env)
                builder_notices.extend(
                    StartupNotice("warn", text)
                    for text in legacy_launch_notices(provider_name, result, payload.policy)
                )
            materialize_consent_wrappers(
                self.project_dir, result, session.session_id, provider, artifacts
            )
            session_env = artifacts.extra_env
        _claim_session_cache(self.project_dir, session.session_id)
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

        # Build CLI command with session ID for JSONL linkage
        claude_session_id = str(uuid.uuid4())
        cmd = assemble_launch_command(
            provider,
            extra_args=extra_args,
            session_args=session_args,
            provider_session_id=claude_session_id,
        )
        # HATS-1397: the argv the provider built is the only honest answer to
        # "is this id ours?", and the link is persisted here rather than at
        # teardown so a killed session still names its transcript.
        claude_session_id = consumed_session_id(cmd, claude_session_id)
        session.record_provider_session_id(claude_session_id)

        # HATS-1216: persist launch record as role_materialization.json
        env_map = assemble_launch_env(
            provider,
            self.project_dir,
            session.session_dir,
            session_id=session.session_id,
            trace_path=str(session.trace_path),
            # HATS-1594: the expression, not the base name — a check bound by a
            # runtime-added trait must resolve for the gate too.
            role=payload.role_expression,
            root_pid=str(os.getpid()),  # HATS-955: ownership liveness anchor
            extra_env=session_env,
            run_mode=RunMode.HITL,
        )
        prompt_file = next(
            (p for p in artifacts.materialized if p.suffix in (".md", ".MD")),
            session.meta_prompt_path if session.meta_prompt_path.is_file() else None,
        )
        # HATS-1548: the same section --dry-run shows, on the launch record — one
        # call site would be a report about a session nobody can compare against.
        reported_checks, check_notes = describe_checks(
            provider, self.project_dir, result, session.session_id, artifacts.port.plan
        )
        builder_notices.extend(StartupNotice("warn", text) for text in check_notes)
        # The record carries the same notes the dry-run does. They are already on
        # their way to the screen as StartupNotices; a launch record that omitted
        # them would disagree with `--dry-run` about the same session.
        report_notes = tuple(n.text for n in builder_notices)
        report = SessionReport(
            role=active_role,
            provider=provider_name,
            run_mode=RunMode.HITL.value,
            policy=payload.policy,
            launch=cmd,
            env=env_map,
            prompt=prompt_file,
            plan=artifacts.port.plan,
            cwd=str(self.project_dir),
            checks=reported_checks,
            consent=result.consent,
            notes=report_notes,
        )
        session.save_role_materialization(report.to_dict())

        session.log_sys(f"Session started: role={active_role}")

        # Log CLI restart gap from previous session (helps judge distinguish
        # restarts from provider stalls).
        self._log_restart_gap(session)

        # The record above IS this environment minus the inherited part — one
        # expression, so the report cannot under-state what the child receives.
        env = {**os.environ, **env_map}

        # HATS-833: fail-open session-start drift net for all managed-hook
        # surfaces; reuses the composition above and returns startup notices.
        startup_notices: list[StartupNotice] = []
        startup_notices.extend(builder_notices)
        startup_notices.extend(self._resync_managed_hooks(session, result))
        startup_notices.extend(self._check_skill_collisions(session, result))
        startup_notices.extend(self._check_skill_script_collisions(session, result))
        startup_notices.extend(self._payload_startup_notices())
        startup_notices.extend(self._lint_provider_settings(session))
        startup_notices.extend(self._lint_env_drift(session))
        startup_notices.extend(self._check_broken_hook_refs(session))
        self._sweep_consent_store(session)
        # HATS-1581. LAST here on purpose: unlike its fail-open neighbours a
        # refusal does not return, so everything above must speak first. And
        # after the launch record, which is what the gate reads.
        startup_notices.extend(
            run_startup_checks(
                self.project_dir,
                session_dir=session.session_dir,
                # HATS-1594: parsed back out of the env just written, so the gate
                # is judged by the very bytes the children will read — and the
                # composition is handed over rather than composed a second time.
                identity=SessionIdentity.from_env(env_map),
                extra_env=env_map,
                compose=lambda _project_dir: result,
            )
        )

        session.log_sys(f"Launching: {' '.join(cmd)}")
        session.append_audit(f"Launched {provider_name} CLI")

        # HATS-566: build the finalize pipeline NOW, against the YAML and the step
        # modules this process holds today. Left to the `finally` block (where
        # ``_run_finalize_hitl`` runs), a session that straddles a working-tree
        # update — editable install plus a mid-session ``git pull`` — reads the
        # *new* YAML against the *old* registry; see the StepRegistryError in
        # session 20260527-085647-1, after the HATS-530 merge landed while the wrap
        # was still alive. HATS-1783 widened what this pins: resolving an id now
        # imports its step module too, so warming freezes the modules as well as
        # the file. Fail-open — a session does not end because its epilogue could
        # not be prepared, and the notice says so.
        try:
            warm(FINALIZE_HITL)
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
        t0 = time.monotonic()
        try:
            # HATS-1221: Save structured startup notices to diagnostics.json
            save_session_diagnostics(
                session.session_dir,
                "startup",
                {
                    "hold_seconds": _startup_hold_seconds(
                        bool(startup_notices),
                        is_tty=sys.stdin.isatty(),
                        env=env,
                    ),
                    "notices": [
                        {
                            "level": n.level,
                            "text": strip_ansi_and_control_codes(n.text),
                        }
                        for n in startup_notices
                    ],
                },
            )
            # HATS-825: brief pre-launch hold so the start banner + any
            # fail-open startup warning are readable before the TUI clobbers
            # them. Ctrl-C here aborts the launch (caught below → exit 130).
            self._hold_before_launch(startup_notices, env=env)
            with provider.execution_context(self.project_dir):
                # HATS-1339: the anchor names the cache's real READER. Redundant
                # here (the pty hangup already ties the child to us), but it makes
                # "keep while EITHER owner lives" hold on every runner.
                exit_code = self._pty_spawn(
                    cmd,
                    env,
                    tracer,
                    pty_tap_factory=pty_tap_factory,
                    on_spawn=lambda pid: _claim_surface_child(
                        self.project_dir, session.session_id, pid
                    ),
                )
        except KeyboardInterrupt:
            exit_code = 130
        finally:
            duration_s = time.monotonic() - t0
            trace_stats: dict = {}
            try:
                # HATS-1426: the terminal is back in cooked mode here, so a
                # stray Ctrl-C would land inside whichever step is running.
                with sigint_shield():
                    try:
                        trace_stats = _finalize_session_basic(
                            session,
                            exit_code=exit_code,
                            active_role=active_role,
                            provider_name=provider_name,
                            tracer=tracer,
                            tags=tags,
                            claude_session_id=claude_session_id,
                            duration_s=duration_s,
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
                                transcript_resolver=payload.transcript_resolver,
                            )
                        except (Exception, KeyboardInterrupt):
                            # HATS-1374: parity with the sub-agent path — a dead sensor
                            # is recorded in the artifact, not only whispered to a log.
                            logger.error("finalize-hitl pipeline failed", exc_info=True)
                            _flag_sensor_error(session)
                    except FinalizeAborted:
                        exit_code = 130
                    finally:
                        # The summary print is the only thing that surfaces the
                        # session id to the user. It MUST run, even on second
                        # SIGINT, even if every step above failed — an abort
                        # drops the remaining work, never the session id.
                        try:
                            _print_session_end(session, trace_stats=trace_stats)
                        except (Exception, KeyboardInterrupt):
                            logger.warning("session-end print failed", exc_info=True)
                            try:
                                print(f"\n✨ Session {session.session_id} complete!")
                            except (BrokenPipeError, OSError):
                                pass
            except FinalizeAborted:
                exit_code = 130

            # HATS-294: drop the per-session cache dir (prompt + plugin/). A
            # SIGKILL leaves it to the next run's sweep, which since HATS-1339
            # reclaims on proof this pid is gone rather than after a TTL — safe
            # only because _pty_spawn's hangup outlives no surface.
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

    def _pty_spawn(
        self,
        cmd: list[str],
        env: dict[str, str],
        tracer: SidecarTracer,
        pty_tap_factory: PtyTapFactory | None = None,
        on_spawn: Callable[[int], None] | None = None,
    ) -> int:
        """Spawn a process with PTY for interactive terminal passthrough + sidecar trace.

        ``on_spawn`` is called once with the child's pid, the moment there is
        one — same seam and same spelling as ``subagent_runner._run_surface``,
        where both runners hand it the surface-child claim and neither spawn
        primitive learns what a session cache is. Omitted → no callback, which
        is exactly what an isolated spawn wants.

        Uses ptyprocess so the slave-pty becomes the controlling-tty of the child
        session (TIOCSCTTY in child after setsid). This is required for nested
        programs (e.g. claude → $EDITOR via Ctrl-G) whose pgrp transfer relies on
        kernel-side tcsetpgrp/setpgid against a real ctty. stdlib pty.spawn does
        not call TIOCSCTTY, which broke that path. See HATS-207.

        That same ctty is load-bearing for HATS-1339: this process is the only
        holder of the pty master, so a SIGKILL here drops carrier and the kernel
        hangs up the surface — which is why the sweep may reclaim a dead owner's
        cache at once without stranding the process that reads it. Spawning over
        pipes, or handing the master fd to anyone else, silently retires that
        guarantee; ``test_a_killed_wrappers_surface_child_goes_with_it`` is what
        notices. Sub-agents get NO such guarantee — see ``subagent_runner``.
        """  # comment-length: allow — one injected seam + two kernel contracts
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

        if on_spawn is not None:
            try:
                on_spawn(proc.pid)
            except Exception as exc:
                # The child already holds the tty; leaving it there because a
                # bookkeeping callback failed would strand an interactive surface
                # with nobody draining its pty.
                logger.warning("session-cache claim skipped for pid %s: %r", proc.pid, exc)

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
        # HATS-1192: one PtyTap per session (single creation, fail-open). Null-object
        # default → no None-guards; a composed plugin (HATS-1197) gets the real tap.
        try:
            self._tap = (
                pty_tap_factory(
                    inject=lambda b: os.write(master_fd, b),
                    # resize drives the CHILD pty window (same call as _on_winch);
                    # local-vs-remote precedence is relay policy (HATS-1197).
                    resize=proc.setwinsize,
                    session=tracer.session,
                )
                if pty_tap_factory is not None
                else NullPtyTap()
            )
        except Exception:  # noqa: BLE001 — a bad plugin must never abort the session
            logger.warning("pty_tap factory raised — no tap for this session", exc_info=True)
            self._tap = NullPtyTap()

        def _drop(where: str, exc: BaseException) -> None:
            # Idempotent: swap to a no-op tap (further ops + close are inert) and
            # close the dead one once. A tap fault never breaks the sacred loop.
            dead, self._tap = self._tap, NullPtyTap()
            logger.warning("pty_tap fault in %s — dropped", where, exc_info=exc)
            try:
                dead.close()
            except Exception:  # noqa: BLE001
                logger.warning("pty_tap close failed after %s fault", where, exc_info=True)

        try:
            while True:
                try:
                    extra = self._tap.extra_read_fds()
                except Exception as exc:  # noqa: BLE001 — fail-open
                    _drop("extra_read_fds", exc)
                    extra = ()
                sel_fds = [*read_fds, *extra] if extra else read_fds
                try:
                    rlist, _, _ = select.select(sel_fds, [], [])
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
                    # OUT tee: bytes already reached the terminal; the tap gets a
                    # copy (never alters local output).
                    try:
                        self._tap.on_output(data)
                    except Exception as exc:  # noqa: BLE001 — fail-open
                        _drop("on_output", exc)

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

                for fd in rlist:
                    if fd == master_fd or fd == stdin_fd:
                        continue
                    try:
                        self._tap.on_readable(fd)
                    except Exception as exc:  # noqa: BLE001 — fail-open
                        _drop("on_readable", exc)
                        break
        finally:
            try:
                self._tap.close()
            except Exception:  # noqa: BLE001 — fail-open
                logger.warning("pty_tap close failed", exc_info=True)
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
