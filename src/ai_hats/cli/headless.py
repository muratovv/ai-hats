"""`ai-hats headless` — a role's session as a UNIX filter over the surface binary (ADR-0038)."""

from __future__ import annotations

import os
import sys

import click

from ._helpers import session_options, with_model_flag


def _machine_stdout() -> int:
    """Take fd 1 for the machine and point fd 1 at stderr, before anything prints —
    so a printer nobody here knows about still cannot reach the log copy."""
    sys.stdout.flush()
    machine = os.dup(1)
    os.dup2(2, 1)
    return machine


def _split_prompt(args: list[str]) -> tuple[str, list[str]]:
    """The positional prompt is the first leftover when it is not a flag; the rest
    is the provider's."""
    if args and not args[0].startswith("-"):
        return args[0], args[1:]
    return "", args


@click.command(
    "headless",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
)
@session_options
@click.argument(
    "passthrough", nargs=-1, type=click.UNPROCESSED, metavar="[PROMPT] [PROVIDER_ARGS]..."
)
def headless_cmd(
    provider: str | None,
    role: str | None,
    model: str | None,
    tags_raw: tuple[str, ...],
    passthrough: tuple[str, ...],
) -> None:
    """Run a role's session driven over stdin/stdout instead of a terminal.

    Same parameters as a bare `ai-hats`; PROMPT, when given, is the first turn.

    \b
    stdin   one command per line: {"v":"commands/v1","cmd":"prompt","text":"…"}
    stdout  line 1 the session header (headless/v1), then events.jsonl, whole
    stderr  everything a person reads
    exit    0 after stdin closes and every turn ran · 130/143 on Ctrl-C/SIGTERM
            · the surface's own code when it died · 2 on a flag headless owns

    \b
        ai-hats headless -r maintainer < turns.ndjson > events.ndjson
        ai-hats headless -r maintainer "summarise README.md" </dev/null
    """
    machine = _machine_stdout()

    from ..composition_seam import build_composition_payload, make_session_manager
    from ..pipeline import run_pipeline
    from ..pipeline_catalog import HEADLESS
    from ..session_policy import (
        Headless,
        MaterializedRole,
        SessionOutcome,
        SessionRecording,
        SessionRunParams,
    )
    from ..tags import TagValidationError, parse_tags
    from ._entry import resolve_project

    if "+" in passthrough:
        raise click.UsageError(
            "bare '+' in provider arguments — a role spec with spaces must be quoted:\n"
            '       ai-hats headless -r "maintainer + leader"'
        )
    prompt, extra = _split_prompt(list(passthrough))
    extra = with_model_flag(model, extra)
    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    layout = resolve_project().layout
    payload = build_composition_payload(
        layout.root, role_override=role, provider_name=provider, interactive=True
    )
    wire = payload.provider.wire()
    if wire is None:
        raise click.UsageError(
            f"ai-hats headless cannot drive {payload.provider.name} yet — it supports: claude"
        )
    owned = wire.owned_in(extra)
    if owned:
        raise click.UsageError(
            f"{owned[0]} is set by ai-hats headless itself: the holder owns the wire "
            "(its formats, its permission channel, and which session it is)"
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
            harness=Headless(prompt=prompt or None, extra_args=tuple(extra), stdout_fd=machine),
        ),
    )
    sys.exit(SessionOutcome.of(result).exit_code_or(1))
