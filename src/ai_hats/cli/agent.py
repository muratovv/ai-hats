"""`ai-hats agent` — launch a sub-agent session with an isolated worktree."""

from __future__ import annotations

import json
import sys

import click

from ._helpers import _project_dir, console


@click.command("agent")
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
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a single JSON object to stdout on completion (session_id, "
         "session_dir, exit_code, role, duration_s, tags, ...). Suppresses "
         "the human-readable summary. Pair with stable exit code propagation "
         "so orchestrators can fan out via parallel/xargs/CI scripts.",
)
def run_subagent(
    role: str,
    ticket: str | None,
    model: str | None,
    task: str | None,
    isolation: str,
    tags_raw: tuple[str, ...],
    as_json: bool,
):
    """Run a sub-agent with the given role.

    Exit codes (propagated from the sub-agent subprocess / runtime):

    - 0   — success
    - 1   — agent/runtime error
    - 2   — CLI usage error (invalid flags)
    - 124 — timeout (sub-agent exceeded wall-clock limit)
    - other non-zero — forwarded verbatim from provider CLI
    """
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

    metrics: dict = {}
    if session.metrics_path.exists():
        try:
            metrics = json.loads(session.metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            metrics = {}

    if as_json:
        # Shape matches `session list --json` item: metrics fields plus
        # computed session_id / session_dir. Consumers pick what they need.
        payload = {
            **metrics,
            "session_id": session.session_id,
            "session_dir": str(session.session_dir),
        }
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"[green]Sub-agent completed[/]: {session.session_id}")
        console.print(f"  Session dir: {session.session_dir}")

    sys.exit(int(metrics.get("exit_code", 1)))
