"""``provider`` step — allocate session + spawn provider.

Renamed from ``launch_provider`` in HATS-535 alongside the split that
extracted audit derivation into the dedicated ``make_audit`` step and
session-end hooks/retro into ``run_session_end`` (both invoked via the
``finalize-hitl`` / ``finalize-subagent`` sub-pipelines from the
runner's ``finally``). Post-split, this step is responsible for spawn
+ per-runner basic finalize (metrics.json, transcripts, cache cleanup)
+ the SIGINT-safe session-end summary print. Audit lives downstream.

ADR-0002 §Step inventory: produces flat keys
``{session_id, session_dir, transcript_path, exit_code}`` so post-steps
(``extract_marker``, ``save_artifact``, ``spawn_session_review``) depend
on path strings, not Session-objects. ``claude_session_id`` is NOT in
the main funnel — the runner passes it directly into
``PipelineHarness.run("finalize-hitl"|"finalize-subagent",
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
            # HATS-865: funnel-seeded CompositionPayload, handed to the runner
            # as-is — this step never composes nor resolves providers.
            requires=frozenset({"interactive", "project_dir", "composition"}),
            # HATS-505: ``system_prompt`` is deliberately NOT read here —
            # prompt delivery goes through the payload, not a funnel string.
            optional=frozenset({
                "prompt_text", "model", "isolation", "ticket", "tags",
                "extra_args",
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
        composition: Any,
        prompt_text: str = "",
        model: str = "",
        isolation: str = "discard",
        ticket: str = "",
        tags: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ...harness.guard import apply_post_run_guard
        from ...runtime import SubAgentRunner, WrapRunner

        if interactive:
            eff_extra = list(extra_args or [])
            if prompt_text:
                eff_extra = [prompt_text, *eff_extra]
            # HATS-452 (П2 in ADR-0005): WrapRunner is HITL — no override
            # channel; the payload's composition reaches the agent via
            # ``build_session_prompt`` inside ``run``.
            runner = WrapRunner(project_dir, composition)
            exit_code, session = runner.run(extra_args=eff_extra, tags=tags)
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

        runner = SubAgentRunner(project_dir, composition)
        # HATS-378: SubAgentRunner internally applies timeout retry and
        # zero-output guard when ``harness_policy`` is supplied — no
        # external guard call needed for the sub-agent branch.
        # HATS-505: no funnel-supplied ``system_prompt_override`` — the
        # override channel stays reserved for explicit HATS-267 callers.
        session = runner.run(
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
