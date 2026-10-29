"""`ai-hats acp` — a role's session driven as an ACP agent."""

from __future__ import annotations

import os
import sys

import click

from ._helpers import session_options, with_model_flag


def _machine_stdout() -> int:
    """Keep fd 1 for JSON-RPC and point the process stdout at stderr.

    A notice printed by someone who does not know about the client must not
    land in the ACP stream.
    """
    sys.stdout.flush()
    machine = os.dup(1)
    os.dup2(2, 1)
    return machine


@click.command("acp")
@session_options
def acp_cmd(
    provider: str | None,
    role: str | None,
    model: str | None,
    tags_raw: tuple[str, ...],
) -> None:
    """Run a role's session as an ACP agent on stdin/stdout.

    \b
        ai-hats acp -p grok -r maintainer
    """
    machine = _machine_stdout()

    from ..composition_seam import build_composition_payload, make_session_manager
    from ..pipeline import run_pipeline
    from ..pipeline_catalog import HEADLESS
    from ..session_policy import (
        Acp,
        MaterializedRole,
        SessionOutcome,
        SessionRecording,
        SessionRunParams,
    )
    from ..tags import TagValidationError, parse_tags
    from ._entry import resolve_project

    extra = with_model_flag(model, [])
    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as exc:
        raise click.BadParameter(str(exc), param_hint="--tag") from exc

    layout = resolve_project().layout
    payload = build_composition_payload(
        layout.root, role_override=role, provider_name=provider, interactive=True
    )
    if payload.provider.name != "grok":
        raise click.UsageError(
            f"ai-hats acp cannot drive {payload.provider.name} yet — it supports: grok"
        )

    from ai_hats_observe import SidecarTracer

    result = run_pipeline(
        HEADLESS,
        SessionRunParams(
            layout=layout,
            role=MaterializedRole(name=role, composition=payload),
            recording=SessionRecording(
                manager=make_session_manager(layout), tracer_factory=SidecarTracer
            ),
            annotations=tags or None,
            harness=Acp(extra_args=tuple(extra), stdout_fd=machine),
        ),
    )
    sys.exit(SessionOutcome.of(result).exit_code_or(1))
