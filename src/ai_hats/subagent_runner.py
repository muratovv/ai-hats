"""Automate runner: headless sub-agent launch (SubAgentRunner).

Extracted from runtime.py (HATS-715); shared helpers live in runtime_common."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from typing import TYPE_CHECKING

from .composition_payload import CompositionPayload
from .constants import PROVIDER_CLAUDE

# The session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .harness.diagnostic import diagnose_silent_session
from .harness.errors import HarnessTimeoutError
from .harness.guard import apply_post_run_guard
from .harness.surface_guard import SurfaceGuard
from ai_hats_wt import IsolationMode, WorktreeManager
from .check_snapshot import describe_checks
from .session_artifacts import (
    BuiltArtifacts,
    CollectedMetrics,
    RunMode,
    assemble_launch_env,
)
from .session_report import SessionReport
from .session_run import SessionRun
from .runtime_common import (
    SUBAGENT_SUBPROCESS_TIMEOUT_S,
    SUBAGENT_EXIT_TIMEOUT,
    SUBAGENT_EXIT_ERROR,
    _claim_session_cache,
    _claim_surface_child,
    _cleanup_session_cache,
    _session_timed_out,
    _finalize_sub_agent,
)

if TYPE_CHECKING:
    from ai_hats_observe import Session, SessionManager
    from .pipeline import HarnessPolicy

logger = logging.getLogger(__name__)


def _run_surface(
    launch: Sequence[str],
    *,
    work_dir: Path,
    env: dict[str, str],
    timeout_s: float,
    on_spawn: Callable[[int], None],
) -> subprocess.CompletedProcess:
    """``subprocess.run(capture_output=True, text=True, timeout=…)``, plus the pid.

    Spelled out because ``run`` never exposes the child's pid, and the pid is
    what the session-cache sweep needs: this child is what READS the cache, and
    with pipes and no controlling tty nothing hangs it up when its parent is
    SIGKILLed — a real ``agy`` was measured still running 45s later, reparented
    to init (HATS-1339 D3). The timeout path mirrors ``run``'s exactly — kill,
    drain, re-raise carrying the output captured so far — because the caller
    reports ``exc.stdout`` / ``exc.stderr`` on a timeout.
    """  # comment-length: allow — a stdlib call re-spelled needs its reason
    with subprocess.Popen(  # noqa: S603 — argv comes from the provider, not a shell
        list(launch),
        cwd=str(work_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            # Inside the guard: Popen.__exit__ only closes the pipes and waits, so
            # a callback that raises out here would block on a surface that runs
            # for hours instead of killing it.
            on_spawn(proc.pid)
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(
                proc.args, timeout_s, output=stdout, stderr=stderr
            ) from exc
        except BaseException:
            proc.kill()
            raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


class SubAgentRunner:
    """SDK-based sub-agent executor.

    HATS-865: a brick — receives the ready :class:`CompositionPayload` from
    the integrator compose seam and never touches the composition layer.
    HATS-867: the observe writer handle (``session_mgr``) is injected too —
    the runner never imports observe at runtime.
    """

    def __init__(
        self,
        layout: ProjectLayout,
        payload: CompositionPayload,
        *,
        session_mgr: "SessionManager",
    ) -> None:
        self.layout = layout
        self.project_dir = layout.root
        self.payload = payload
        self.session_mgr = session_mgr

    def run(
        self,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
        isolation_mode: str = IsolationMode.DISCARD.value,
        tags: dict[str, str] | None = None,
        system_prompt_override: str | None = None,
        harness_policy: "HarnessPolicy | None" = None,
    ) -> Session:
        """Execute a sub-agent in isolation (role = ``payload.effective_role``).

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

        HATS-865 recorded delta: retry attempts share the ONE payload
        composition (pre-865 each ``_run_attempt`` re-composed) — an
        improvement for attempt comparability.
        """
        on_timeout = harness_policy.on_timeout if harness_policy is not None else None
        max_attempts = 1 + (on_timeout.retry if on_timeout is not None else 0)

        last_session: Session | None = None
        for attempt in range(1, max_attempts + 1):
            if attempt == 1 or on_timeout is None:
                timeout_s = SUBAGENT_SUBPROCESS_TIMEOUT_S
            else:
                timeout_s = int(SUBAGENT_SUBPROCESS_TIMEOUT_S * on_timeout.budget_multiplier)
            attempt_tags = dict(tags or {})
            if attempt > 1:
                attempt_tags["harness_retry_attempt"] = str(attempt)

            last_session = self._run_attempt(
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

        assert last_session is not None  # noqa: S101 — narrow for mypy; the loop body always assigns

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

        * **Legacy subprocess** path (Agy, future providers): unchanged
          ``subprocess.run`` flow. ``subprocess.TimeoutExpired`` keeps its
          long-standing finalize semantics here.

        Timeout and other failure modes are surfaced via metrics fields
        (``timed_out``, ``error``, ``exit_code``) so the outer retry loop
        can inspect them without exception plumbing.
        """
        with SessionRun.create(
            self.session_mgr,
            parent_session=parent_session,
        ) as run:
            session, work_dir = self._run_session_attempt(
                run,
                task=task,
                ticket_id=ticket_id,
                model=model,
                isolation_mode=isolation_mode,
                tags=tags,
                system_prompt_override=system_prompt_override,
                timeout_s=timeout_s,
            )

        SurfaceGuard.post_flight_guard(
            session,
            work_dir,
            self.payload.provider.name,
        ).unwrap()
        return session

    def _run_session_attempt(
        self,
        run: SessionRun,
        *,
        task: str,
        ticket_id: str,
        model: str,
        isolation_mode: str,
        tags: dict[str, str],
        system_prompt_override: str | None,
        timeout_s: int,
    ) -> tuple[Session, Path]:
        session = run.session
        run.defer(
            "session cache",
            lambda: _cleanup_session_cache(self.project_dir, session.session_id),
        )

        # The ONE composition arrived in the payload (compose seam).
        role_name = self.payload.effective_role
        result = self.payload.result
        # HATS-505 / HATS-452 trap: ``with_injection_override`` REPLACES
        # ``result.injections`` WHOLESALE — every overlay contribution (global +
        # project ``injection_append``, ``add_traits`` bodies) is dropped from
        # the SDK system_prompt. The pipeline no longer feeds an override here
        # (HATS-505 a); the only legitimate caller is a HATS-267 explicit-prompt
        # API consumer. A new caller's override text MUST already contain
        # everything the role would compose — or compose first and pass an
        # *augmented* (not replacement) string. Layered ``result`` is above.
        if system_prompt_override is not None:
            # Explicit immutable transformation via the typed
            # ``with_*`` API on ``CompositionResult`` (D1 in ADR-0005).
            result = result.with_injection_override(system_prompt_override)
        provider = self.payload.provider
        provider_name = provider.name
        _ctx = provider.execution_context(self.project_dir)
        _ctx.__enter__()

        artifacts = provider.build_session_artifacts(
            self.project_dir,
            result,
            session.session_id,
            run_mode=RunMode.AUTOMATE,
            policy=self.payload.policy,
            artifacts=BuiltArtifacts(resources=run),
        )
        for warning in artifacts.notices:
            session.log_sys(warning)
        _claim_session_cache(self.project_dir, session.session_id)

        # The gates this sub-agent runs under. Every AUTOMATE record ever written
        # said `checks: []`, so the reflect loop could not see whether a
        # sub-agent had its gates at all.
        reported_checks, notes = describe_checks(
            provider, self.project_dir, result, session.session_id, artifacts.port.plan
        )
        # Everything ai-hats adds to the child's environment, expressed once
        # — the sub-agent path merged its own subset and reported a
        # different one: `extra_env` was reported and never delivered, while the
        # six keys it did deliver appeared in no record.
        launch_env = assemble_launch_env(
            provider,
            self.project_dir,
            session.session_dir,
            session_id=session.session_id,
            trace_path=str(session.trace_path),
            # The expression, not the base name `role_name` reports.
            role=self.payload.role_expression,
            root_pid=str(os.getpid()),  # Ownership liveness anchor
            extra_env=artifacts.extra_env,
            run_mode=RunMode.AUTOMATE,
        )
        described = provider.describe_automate_launch(
            self.project_dir,
            result,
            session.session_id,
            artifacts,
            task=task,
            ticket_id=ticket_id,
            model=model,
            env=launch_env,
        )
        meta_prompt = described.prompt

        session.save_meta_prompt(meta_prompt)
        session.init_audit(
            role=role_name,
            provider=provider.name,
            model=model,
            composition=self.payload.snapshot,
        )

        # Persist launch record as role_materialization.json
        prompt_file = next(
            (p for p in artifacts.materialized if p.suffix in (".md", ".MD")),
            session.meta_prompt_path if session.meta_prompt_path.is_file() else None,
        )
        report = SessionReport(
            role=role_name,
            provider=provider.name,
            run_mode=RunMode.AUTOMATE.value,
            policy=self.payload.policy,
            launch=described.launch,
            env=launch_env,
            prompt=prompt_file,
            plan=artifacts.port.plan,
            cwd="<worktree, assigned at launch>",
            checks=reported_checks,
            consent=result.consent,
            notes=tuple(notes),
        )
        session.save_role_materialization(report.to_dict())

        session.log_sub(f"Sub-agent started: role={role_name}")

        # HATS-474 review fix: a *subprocess* (Agy path) gets the full inherited
        # environment — subprocess.run replaces the child env wholesale when
        # given one. The SDK path takes `launch_env` as an *overlay* it merges
        # on top of os.environ itself, so handing it only ai-hats keys keeps the
        # secret-exposure surface off a long-lived, repr-able options object.
        env = {**os.environ, **launch_env}

        if provider_name != PROVIDER_CLAUDE:
            session.log_sub(f"Executing: {' '.join(described.launch)}")

        mode = IsolationMode(isolation_mode)
        session.log_sub(f"Isolation: {mode.value}")

        # ADR-0013 D3: the context-manager cleanup() fires before_teardown from
        # __exit__, so the manager must carry ai-hats's hook-running bundle.
        from .paths import worktrees_dir
        from .wt_lifecycle import HOOK_LIFECYCLE

        with WorktreeManager(
            self.project_dir,
            role_name,
            session.session_id,
            mode,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=worktrees_dir(self.project_dir),
        ) as work_dir:
            session.log_sub(f"Working directory: {work_dir}")
            SurfaceGuard.pre_flight_check(self.project_dir, work_dir, mode, provider_name).unwrap()
            t0 = time.monotonic()

            # One bundle for all four finalize paths — per-site spelling let the
            # timeout/error paths drift and lose enrichment entirely.
            observe_kwargs = {
                "work_dir": work_dir,
                "static_cost_analyzer": self.payload.static_cost_analyzer,
                "session_factory": self.payload.session_factory,
                "audit_writer_factory": self.payload.audit_writer_factory,
                "transcript_resolver": self.payload.transcript_resolver,
            }

            try:
                engine = provider.engine()
                if engine is not None:
                    metrics = CollectedMetrics()
                    run_result = engine.run(
                        result=result,
                        project_dir=self.project_dir,
                        work_dir=work_dir,
                        session_id=session.session_id,
                        task=task,
                        ticket_id=ticket_id,
                        env=launch_env,
                        model=model,
                        timeout_s=timeout_s,
                        metrics=metrics,
                        artifacts=artifacts,
                    )
                    session.log_res(f"Exit code: {run_result.exit_code}")
                    surface_session_id = metrics.values.get("claude_session_id")
                    if surface_session_id:
                        session.log_sub(f"Provider session_id: {surface_session_id}")
                    _finalize_sub_agent(
                        session,
                        role=role_name,
                        provider=provider_name,
                        model=model,
                        isolation_mode=mode.value,
                        exit_code=run_result.exit_code,
                        stdout=run_result.stdout,
                        stderr=run_result.stderr,
                        timed_out=run_result.timed_out,
                        error=run_result.error,
                        tags=tags,
                        duration_s=time.monotonic() - t0,
                        extra_metrics=metrics.values,
                        **observe_kwargs,
                    )
                else:
                    # Legacy subprocess path (Agy and future non-SDK providers).
                    # The reported argv IS the executed one — this used to
                    # re-derive it from materialize_runtime_skills, and matched
                    # what was reported only by coincidence.
                    with provider.execution_context(self.project_dir):
                        proc = _run_surface(
                            described.launch,
                            work_dir=work_dir,
                            env=env,
                            timeout_s=timeout_s,
                            on_spawn=lambda pid: _claim_surface_child(
                                self.project_dir, session.session_id, pid
                            ),
                        )
                    session.log_res(f"Exit code: {proc.returncode}")
                    stdout_str = proc.stdout or ""
                    extra_metrics: dict = {}
                    if stdout_str.strip().startswith("{") and stdout_str.strip().endswith("}"):
                        try:
                            parsed_json = json.loads(stdout_str.strip())
                            if isinstance(parsed_json, dict):
                                if isinstance(parsed_json.get("response"), str):
                                    stdout_str = parsed_json["response"]
                                usage = parsed_json.get("usage")
                                if isinstance(usage, dict):
                                    extra_metrics["tokens"] = {
                                        "input": usage.get("input_tokens", 0),
                                        "output": usage.get("output_tokens", 0),
                                        "cache_read": usage.get("cache_read_tokens", 0),
                                        "cache_creation": usage.get("cache_creation_tokens", 0),
                                    }
                                if cid := parsed_json.get("conversation_id"):
                                    extra_metrics["provider_session_id"] = str(cid)
                                for key in ("num_turns", "duration_seconds"):
                                    if (val := parsed_json.get(key)) is not None:
                                        extra_metrics[key] = val
                        except (json.JSONDecodeError, ValueError) as exc:
                            logger.debug("could not parse sub-agent stdout as JSON: %s", exc)

                    _finalize_sub_agent(
                        session,
                        role=role_name,
                        provider=provider_name,
                        model=model,
                        isolation_mode=mode.value,
                        exit_code=proc.returncode,
                        stdout=stdout_str,
                        stderr=proc.stderr or "",
                        tags=tags,
                        duration_s=time.monotonic() - t0,
                        extra_metrics=extra_metrics if extra_metrics else None,
                        **observe_kwargs,
                    )

            except subprocess.TimeoutExpired as exc:
                session.log_sys(f"Sub-agent timed out after {timeout_s}s")
                _finalize_sub_agent(
                    session,
                    role=role_name,
                    provider=provider_name,
                    model=model,
                    isolation_mode=mode.value,
                    exit_code=SUBAGENT_EXIT_TIMEOUT,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                    timed_out=True,
                    tags=tags,
                    duration_s=time.monotonic() - t0,
                    **observe_kwargs,
                )
            except Exception as e:
                # Catches any unanticipated SDK-path exception too — defence
                # in depth. ``run_claude_sdk_blocking`` is designed not to
                # raise, but ``asyncio.run`` itself can fail in weird envs
                # (running event loop, etc.) — surface as a clean error.
                session.log_sys(f"Sub-agent error: {e}")
                _finalize_sub_agent(
                    session,
                    role=role_name,
                    provider=provider_name,
                    model=model,
                    isolation_mode=mode.value,
                    exit_code=SUBAGENT_EXIT_ERROR,
                    error=str(e),
                    tags=tags,
                    duration_s=time.monotonic() - t0,
                    **observe_kwargs,
                )
            finally:
                # Release-on-finish BEFORE the cache sweep, so a
                # sibling sub-agent sharing this runner's pid can reclaim the
                # task. Fail-open, so the sweep below always runs.
                self._release_ownership_on_finish(session)
                _ctx.__exit__(None, None, None)

        return session, work_dir

    def _release_ownership_on_finish(self, session: "Session") -> None:
        """Drop this finished session's ownership holds (HATS-1045).

        Sequential sub-agents share this runner's ``os.getpid()`` via
        ``ENV_ROOT_PID``, so a finished session's hold reads live to
        ``record_is_live`` and refuses a sibling's reclaim. Match on
        ``(session_id, os.getpid())`` so an id-colliding peer process is never
        touched. Fail-open: an ownership error must never mask the sub-agent
        result nor skip the session-cache sweep.
        """
        try:
            from . import ownership
            from .tracker_wiring import tracker_paths

            registry = tracker_paths(self.project_dir).tasks_dir.parent / "ownership.json"
            ownership.release_session_pid(registry, session.session_id, os.getpid())
        except Exception as exc:  # noqa: BLE001 — fail-open teardown
            session.log_sys(f"release-on-finish failed: {exc}")
