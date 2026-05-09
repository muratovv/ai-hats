"""``LaunchProvider`` step — atomic blackbox wrapper over ``_do_execute``.

In Phase 1 this step bundles role composition, prompt resolution, PTY/
subprocess spawn, and finalize — same as the current ``_do_execute`` and
runner code paths. Phase 2 will pull composition + prompt-resolve out as
pre-steps; Phase 3 will pull finalize out as post-steps. After that
``LaunchProvider`` shrinks to spawn+wait.
"""

from __future__ import annotations

import json
from typing import Any

from ..step import Step, StepIO


class LaunchProvider(Step):
    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="launch_provider",
            requires=frozenset({"interactive"}),
            optional=frozenset({
                "role", "provider", "prompt_text",
                "model", "isolation", "ticket", "tags", "extra_args",
            }),
            produces=frozenset({"session", "exit_code"}),
        )

    def run(
        self,
        *,
        interactive: bool,
        role: str | None = None,
        provider: str | None = None,
        prompt_text: str | None = None,
        model: str = "",
        isolation: str = "discard",
        ticket: str = "",
        tags: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        # Local import: pipeline must not pull cli.execute at module load —
        # the CLI graph imports providers/runtime which is heavy.
        from ...cli.execute import _do_execute

        result = _do_execute(
            role=role,
            provider=provider,
            interactive=interactive,
            prompt=prompt_text,
            model=model,
            isolation=isolation,
            ticket=ticket,
            tags=tags,
            extra_args=extra_args,
        )

        if interactive:
            return {"session": None, "exit_code": int(result)}

        exit_code = 1
        if result.metrics_path.exists():
            try:
                metrics = json.loads(result.metrics_path.read_text())
                exit_code = int(metrics.get("exit_code", 1))
            except (OSError, ValueError):
                exit_code = 1
        return {"session": result, "exit_code": exit_code}
