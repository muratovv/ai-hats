"""``launch_provider`` step — allocate session + spawn provider + finalize.

ADR-0002 §Step inventory: produces flat keys
``{session_id, session_dir, transcript_path, exit_code}`` so post-steps
(``extract_marker``, ``save_artifact``, ``spawn_session_review``) depend
on path strings, not Session-objects.

The step calls ``WrapRunner``/``SubAgentRunner`` directly rather than
going through ``cli.execute._do_execute`` — keeping the pipeline path
decoupled from the legacy CLI dispatch (per ADR-0002 §Decoupling).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class LaunchProvider(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="launch_provider",
            requires=frozenset({"interactive", "project_dir"}),
            optional=frozenset({
                "role", "provider", "system_prompt", "prompt_text",
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
        system_prompt: str | None = None,
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
            exit_code, session = runner.run(
                eff_provider,
                role_override=role,
                extra_args=eff_extra,
                tags=tags,
                system_prompt_override=system_prompt,
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
        session = runner.run(
            role_name=role or "",
            task=prompt_text,
            ticket_id=ticket,
            model=model,
            isolation_mode=isolation,
            tags=tags,
            system_prompt_override=system_prompt,
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
