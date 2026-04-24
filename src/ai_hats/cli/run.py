"""`ai-hats run` — launch a sub-agent session with an isolated worktree."""

from __future__ import annotations

import click

from ._helpers import _project_dir, console


@click.command("run")
@click.argument("role")
@click.option("--ticket", default=None, help="Ticket/task ID for context")
@click.option("--model", default=None, help="Model override")
@click.option("--task", default=None, help="Task description")
@click.option(
    "--isolation",
    default="discard",
    type=click.Choice(["discard", "squash", "branch"]),
    help="Worktree isolation mode (default: discard)",
)
@click.option(
    "--tag",
    "tags_raw",
    multiple=True,
    help="Custom tag k=v (repeatable, max 20). Stored in metrics.json under 'tags' "
         "for later query via 'ai-hats session list --tag k=v'.",
)
def run_subagent(
    role: str,
    ticket: str | None,
    model: str | None,
    task: str | None,
    isolation: str,
    tags_raw: tuple[str, ...],
):
    """Run a sub-agent with the given role."""
    from ..runtime import SubAgentRunner
    from ..tags import TagValidationError, parse_tags

    try:
        tags = parse_tags(tags_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    runner = SubAgentRunner(_project_dir())
    session = runner.run(
        role_name=role,
        task=task or "",
        ticket_id=ticket or "",
        model=model or "",
        isolation_mode=isolation,
        tags=tags or None,
    )
    console.print(f"[green]Sub-agent completed[/]: {session.session_id}")
    console.print(f"  Session dir: {session.session_dir}")
