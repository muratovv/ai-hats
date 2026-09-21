"""The projections of a plan under ``-re`` (ADR-0034 D5, ADR-0036 D5):
``dry-run`` renders the record, ``show-prompt`` prints the prompt bytes.

Both read the one value processing produced; neither assembles anything.
The root flag selects the candidate DSL and processing, so each command
refuses without it and names the reference path's spelling instead.
"""

from __future__ import annotations

import sys

import click

from ._helpers import console


def _under_re(ctx: click.Context, this: str, reference: str) -> None:
    root = ctx.find_root()
    if not root.params.get("experimental"):
        raise click.UsageError(
            f"`{this}` is a projection of the candidate path and runs under -re:\n"
            f'    ai-hats -re {this} "<expr>"\n'
            f"For the reference path use:\n    {reference}",
            ctx=ctx,
        )


@click.command("dry-run")
@click.argument("expr")
@click.option("--json", "as_json", is_flag=True, help="The record as a value.")
@click.option("--full", "full", is_flag=True, help="Dump the prompt body, not just its path.")
@click.pass_context
def dry_run(ctx: click.Context, expr: str, as_json: bool, full: bool) -> None:
    """Print the plan a launch of EXPR would apply — the candidate processing's
    projection (ADR-0036 D5); spawn nothing, write nothing.

    \b
        ai-hats -re dry-run "hypothesis-intake + worker"
        ai-hats -re dry-run "hypothesis-intake + worker" --json
    """
    import json as _json

    from ..session_artifacts import RunMode
    from ..session_plan import preview, render_record
    from ._entry import resolve_project

    _under_re(ctx, "dry-run", "ai-hats --dry-run -r <role>")
    shown = preview(
        resolve_project().layout,
        role=expr,
        provider=ctx.find_root().params.get("provider"),
        run_mode=RunMode.HITL,
        experimental=True,
    )
    if as_json:
        click.echo(_json.dumps(shown.record, indent=2))
    else:
        click.echo(render_record(shown.record, full=full, prompt_text=shown.prompt), nl=False)


@click.command("show-prompt")
@click.argument("expr")
@click.pass_context
def show_prompt(ctx: click.Context, expr: str) -> None:
    """Print the prompt the agent would read for EXPR — the surface's
    ``prompt.text`` of the candidate plan (ADR-0036 D5).

    \b
        ai-hats -re show-prompt worker
        ai-hats -re show-prompt "hypothesis-intake + worker" | grep WORKER
    """
    from ..composition_seam import build_preview_payload
    from ..diagnostics import emit_to_stderr
    from ..processing import ProcessingError
    from ._entry import resolve_project
    from .assembly import preview_pipeline

    _under_re(ctx, "show-prompt", "ai-hats config show-prompt --role <role>")
    pipeline = preview_pipeline(stats=False)
    try:
        payload = build_preview_payload(
            resolve_project().layout.root,
            role=expr,
            provider=ctx.find_root().params.get("provider"),
            experimental=True,
        )
        emit_to_stderr(payload.diagnostics)
        pipeline.run(composition=payload)
    except ProcessingError:
        raise  # rendered by the root group's friendly-error registry
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)
