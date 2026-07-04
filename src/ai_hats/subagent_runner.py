"""Automate runner: headless sub-agent launch (SubAgentRunner).

Extracted from runtime.py (HATS-715); shared helpers live in runtime_common."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from typing import TYPE_CHECKING

from .composition_payload import CompositionPayload

# HATS-649: the session-cache sweep moved to ``environment_recovery`` so it sits
# beside the other recovery passes (bundled and run at the create_session
# chokepoint). Re-exported so existing callers/tests keep importing it from
# ``ai_hats.runtime``.
from .environment_recovery import _sweep_orphan_session_caches  # noqa: F401
from .harness.diagnostic import diagnose_silent_session
from .harness.errors import HarnessTimeoutError
from .harness.guard import apply_post_run_guard
from .observe import Session, SessionManager, TraceTag
from ai_hats_wt import IsolationMode, WorktreeManager
from .runtime_common import (
    SUBAGENT_SUBPROCESS_TIMEOUT_S,
    SUBAGENT_EXIT_TIMEOUT,
    SUBAGENT_EXIT_ERROR,
    _cleanup_session_cache,
    _session_timed_out,
    _finalize_sub_agent,
)

if TYPE_CHECKING:
    from .pipeline.harness_policy import HarnessPolicy

logger = logging.getLogger(__name__)


class SubAgentRunner:
    """SDK-based sub-agent executor.

    HATS-865: a brick — receives the ready :class:`CompositionPayload` from
    the integrator compose seam and never touches the composition layer.
    """

    def __init__(self, project_dir: Path, payload: CompositionPayload) -> None:
        self.project_dir = project_dir
        self.payload = payload
        self.session_mgr = SessionManager(project_dir)

    def run(
        self,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
        isolation_mode: str = "discard",
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

        # HATS-865: the ONE composition arrived in the payload (compose seam).
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
            # HATS-452: explicit immutable transformation via the typed
            # ``with_*`` API on ``CompositionResult`` (П1 in ADR-0005).
            result = result.with_injection_override(system_prompt_override)
        provider = self.payload.provider
        provider_name = provider.name

        # HATS-474: for the Claude path the meta-prompt stored on disk is
        # a *forensic* artifact — it records what we actually sent to the
        # SDK (system_prompt + initial user message), not what the legacy
        # ``-p`` arg would have looked like. For non-Claude providers we
        # keep the legacy structure intact.
        if provider_name == "claude":
            meta_prompt = self._build_sdk_prompt_audit(
                result=result,
                task=task,
                ticket_id=ticket_id,
            )
        else:
            meta_prompt = self._build_meta_prompt(
                result=result,
                provider=provider,
                task=task,
                ticket_id=ticket_id,
            )
        session.save_meta_prompt(meta_prompt)
        session.init_audit(
            role=role_name,
            provider=provider.name,
            model=model,
            composition=self.payload.snapshot,
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
                self.project_dir,
                result,
                session.session_id,
            )
            cmd = cmd + skill_args
            session.log_trace(TraceTag.SUB, f"Executing: {' '.join(cmd)}")

        mode = IsolationMode(isolation_mode)
        session.log_trace(TraceTag.SUB, f"Isolation: {mode.value}")

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
                        TraceTag.RES,
                        f"Exit code: {run_result.exit_code}",
                    )
                    if run_result.claude_session_id:
                        session.log_trace(
                            TraceTag.SUB,
                            f"Claude session_id: {run_result.claude_session_id}",
                        )
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
                        extra_metrics={
                            "claude_session_id": run_result.claude_session_id,
                            "total_cost_usd": run_result.total_cost_usd,
                            "num_turns": run_result.num_turns,
                            "stop_reason": run_result.stop_reason,
                        },
                        work_dir=work_dir,
                        static_cost_analyzer=self.payload.static_cost_analyzer,
                    )
                else:
                    # Legacy subprocess path (Gemini and future non-SDK providers).
                    full_cmd = provider.get_run_command(
                        cmd,
                        meta_prompt,
                        model=model or None,
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
                        provider=provider_name,
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
                    provider=provider_name,
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
                    provider=provider_name,
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
        linked_context = self._load_linked_context(ticket_id)

        options = build_options(
            result,
            provider=self.payload.provider,
            project_dir=self.project_dir,
            session_id=session_id,
            work_dir=work_dir,
            model=model or "",
            extra_env=env or None,
        )
        # HATS-681: PROJECT_STATE (the STATE.md backlog dump) is no longer
        # injected — it was unused dead weight in every sub-agent run.
        # HATS-689: LINKED_CONTEXT carries the directly-linked cards (this is
        # the live Claude channel for that section).
        initial_message = build_first_user_message(
            ticket_context=ticket_context,
            linked_context=linked_context,
            task=task,
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

        sp = _build_system_prompt(result, self.project_dir, self.payload.provider)
        system_text = sp.get("append", "")
        initial_message = build_first_user_message(
            ticket_context=self._load_ticket(ticket_id),
            linked_context=self._load_linked_context(ticket_id),
            task=task,
        )
        return (
            "==== SDK system_prompt (preset=claude_code, append) ====\n"
            f"{system_text}\n"
            "\n"
            "==== SDK first user message ====\n"
            f"{initial_message}\n"
        )

    def _build_meta_prompt(self, result, provider, task: str, ticket_id: str) -> str:
        """Build the meta-prompt for sub-agent execution."""
        from .placeholders import expand_path_placeholders

        sections = []

        # SYSTEM_ROLE — HATS-380: expand <ai_hats_dir> before the role/trait
        # injection reaches the sub-agent inline. Canonical writer and provider
        # build_session_prompt paths already expand; meta-prompt was the residual gap
        # (roles like session-reviewer carry literal <ai_hats_dir> in injection).
        merged = expand_path_placeholders(result.merged_injection, self.project_dir)
        sections.append(f"# SYSTEM_ROLE\n{merged}")

        # HATS-681: PROJECT_STATE (the STATE.md backlog dump) is intentionally
        # NOT injected. On-data verification (154 prompts) showed it was ~5.4K
        # tok of mostly-completed-task dead weight per sub-agent run, unused by
        # the dominant consumer (session-reviewer). Sub-agents reach the backlog
        # on-demand via the `ai-hats task` CLI.

        # CONSTRAINTS
        if result.priorities:
            constraints = "\n".join(f"- {p}" for p in result.priorities)
            sections.append(f"# CONSTRAINTS\n{constraints}")

        # TICKET_CONTEXT
        if ticket_id:
            ticket_context = self._load_ticket(ticket_id)
            if ticket_context:
                sections.append(f"# TICKET_CONTEXT\n{ticket_context}")

            # LINKED_CONTEXT (HATS-689) — directly-linked cards (parent epic +
            # plan.md, plus depends_on/related/see_also). Live Gemini channel;
            # the Claude path mirrors this via build_first_user_message.
            linked_context = self._load_linked_context(ticket_id)
            if linked_context:
                sections.append(f"# LINKED_CONTEXT\n{linked_context}")

        # TASK
        if task:
            sections.append(f"# TASK\n{task}")

        return "\n\n".join(sections)

    def _load_ticket(self, ticket_id: str) -> str:
        """Load ticket context from task card (delegates to ``linked_context``)."""
        from .linked_context import load_ticket

        return load_ticket(self.project_dir, ticket_id)

    def _load_linked_context(self, ticket_id: str) -> str:
        """Assemble the ``LINKED_CONTEXT`` body for a ticket's direct links.

        HATS-689 logic now lives in :mod:`ai_hats.linked_context` so the same
        assembly serves both the sub-agent prompt (here) and ``ai-hats task
        show`` (HATS-691) — a single seam, not two divergent paths.
        """
        from .linked_context import load_linked_context

        return load_linked_context(self.project_dir, ticket_id)
