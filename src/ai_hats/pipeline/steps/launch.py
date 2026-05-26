"""``provider`` step — allocate session + spawn provider.

Renamed from ``launch_provider`` in HATS-535 alongside the split that
extracted audit derivation into the dedicated ``make_audit`` step and
session-end hooks/retro into ``run_session_end`` (both invoked via the
``finalize-hitl`` / ``finalize-subagent`` sub-pipelines from the
runner's ``finally``). Post-split, this step is responsible for spawn
+ per-runner basic finalize (metrics.json, transcripts, cache cleanup)
+ the SIGINT-safe session-end summary print. Audit + lifecycle hooks
live downstream.

ADR-0002 §Step inventory: produces flat keys
``{session_id, session_dir, transcript_path, exit_code}`` so post-steps
(``extract_marker``, ``save_artifact``, ``spawn_session_review``) depend
on path strings, not Session-objects. ``claude_session_id`` and
``hooks_env`` are NOT in the main funnel — the runner passes them
directly into ``PipelineHarness.run("finalize-hitl"|"finalize-subagent",
initial=...)`` from its ``finally`` block.

The step calls ``WrapRunner``/``SubAgentRunner`` directly rather than
going through ``cli.execute._do_execute`` — keeping the pipeline path
decoupled from the legacy CLI dispatch (per ADR-0002 §Decoupling).

``LaunchProvider`` is retained as a deprecated alias for backwards
compatibility with externally-loaded YAML pipelines that still
reference ``id: launch_provider``. Prefer ``Provider`` / ``id:
provider`` in new code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class Provider(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="provider",
            requires=frozenset({"interactive", "project_dir"}),
            # HATS-505: ``system_prompt`` is no longer in the optional set.
            # ``ComposeRole`` still emits it for ``PreLog`` (observability),
            # but ``LaunchProvider`` deliberately does not read it — neither
            # the HITL branch (composes via ``WrapRunner.build_session_prompt``)
            # nor the sub-agent branch (composes via
            # ``SubAgentRunner._run_attempt → compose_for_role``) needs a
            # funnel-supplied prompt. ``SubAgentRunner.run``'s
            # ``system_prompt_override`` parameter survives for explicit
            # HATS-267 callers (e.g. ``subagent_session.py``); the pipeline
            # is no longer one of them.
            optional=frozenset({
                "role", "provider", "prompt_text",
                "model", "isolation", "ticket", "tags", "extra_args",
            }),
            produces=frozenset({
                "session_id", "session_dir", "transcript_path", "exit_code",
            }),
        )

    def run(
        self,
        *,
        interactive: bool,
        project_dir: Path,
        role: str | None = None,
        provider: str | None = None,
        prompt_text: str = "",
        model: str = "",
        isolation: str = "discard",
        ticket: str = "",
        tags: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ...harness.guard import apply_post_run_guard
        from ...models import ProjectConfig
        from ...runtime import SubAgentRunner, WrapRunner

        if interactive:
            cfg = ProjectConfig.from_yaml(project_dir / "ai-hats.yaml")
            eff_provider = provider or cfg.provider
            if not eff_provider:
                raise RuntimeError(
                    "launch_provider: no provider configured. "
                    "Run: ai-hats config set -p <provider>"
                )
            eff_extra = list(extra_args or [])
            if prompt_text:
                eff_extra = [prompt_text, *eff_extra]
            runner = WrapRunner(project_dir)
            # HATS-452 (П2 in ADR-0005): WrapRunner is HITL — no override
            # channel. The role's full composition reaches the agent via
            # ``build_session_prompt`` inside ``run_session``. HATS-505:
            # the sub-agent branch below also no longer consumes a
            # funnel-supplied ``system_prompt`` — only explicit HATS-267
            # callers (e.g. ``subagent_session.py``) use
            # ``SubAgentRunner.run``'s ``system_prompt_override`` channel.
            exit_code, session = runner.run(
                eff_provider,
                role_override=role,
                extra_args=eff_extra,
                tags=tags,
            )
            # HATS-378: universal zero-output guard for reporting steps.
            # Interactive (main) sessions have trace-enriched metrics by
            # the time WrapRunner returns, so the token/tool_calls
            # criterion is reliable here.
            apply_post_run_guard(session, self.harness_policy)
            return {
                "session_id": session.session_id,
                "session_dir": session.session_dir,
                "transcript_path": session.trace_path,
                "exit_code": int(exit_code),
            }

        runner = SubAgentRunner(project_dir)
        # HATS-378: SubAgentRunner internally applies timeout retry and
        # zero-output guard when ``harness_policy`` is supplied — no
        # external guard call needed for the sub-agent branch.
        # HATS-505: do NOT forward a funnel-supplied prompt as
        # ``system_prompt_override``. The runner composes the role itself
        # via ``compose_for_role`` (full overlay layering); pre-feeding the
        # override would only re-apply the same composition (no value) while
        # leaving the HATS-452-class trap (``with_injection_override``
        # wholesale-replace) wired to a pipeline funnel value. The override
        # parameter on ``SubAgentRunner.run`` survives for explicit HATS-267
        # callers, which the pipeline is not.
        session = runner.run(
            role_name=role or "",
            task=prompt_text,
            ticket_id=ticket,
            model=model,
            isolation_mode=isolation,
            tags=tags,
            harness_policy=self.harness_policy,
        )
        exit_code = 1
        if session.metrics_path.exists():
            try:
                metrics = json.loads(session.metrics_path.read_text())
                exit_code = int(metrics.get("exit_code", 1))
            except (OSError, ValueError):
                exit_code = 1
        # Non-interactive: sub-agent stdout lands in transcript.txt
        # (written by _finalize_sub_agent). trace.log only carries
        # SUB/RES system events, so extract_marker on it would miss
        # the LLM output. Fall back to trace_path if transcript.txt
        # was not produced (e.g. sub-agent terminated before stdout).
        transcript_txt = session.session_dir / "transcript.txt"
        transcript_path = (
            transcript_txt if transcript_txt.exists() else session.trace_path
        )
        return {
            "session_id": session.session_id,
            "session_dir": session.session_dir,
            "transcript_path": transcript_path,
            "exit_code": exit_code,
        }


# HATS-535: ``LaunchProvider`` kept as a deprecated alias so external YAML
# pipelines referencing ``id: launch_provider`` keep loading. The class
# is identical to ``Provider`` (just a re-export); the registry maps both
# names to it. Prefer ``Provider`` / ``id: provider`` in new code.
LaunchProvider = Provider
