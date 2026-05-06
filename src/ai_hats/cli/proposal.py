"""`ai-hats task proposal` — manage proposal backlog (.agent/backlog/proposals/).

Subcommands:
  list, show, create, vote, status

Proposals are improvement suggestions emitted by reflect-session. Status
field regulates visibility — accepted/rejected/deferred proposals stay on
disk for traceability.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import click

from ..hypothesis import (
    Proposal,
    ProposalStore,
    Vote,
    next_proposal_id,
)
from ._helpers import _project_dir, console


def _proposals_dir(project_dir: Path) -> Path:
    return project_dir / ".agent" / "backlog" / "proposals"


def _store(project_dir: Path | None = None) -> ProposalStore:
    pd = project_dir or _project_dir()
    return ProposalStore(_proposals_dir(pd))


@click.group()
def proposal():
    """Manage proposal backlog."""


@proposal.command("list")
@click.option(
    "--status",
    type=click.Choice(["open", "accepted", "rejected", "deferred", "duplicate"]),
    default=None,
)
@click.option("--category", default=None)
@click.option("--target", default=None)
@click.option("--json", "as_json", is_flag=True)
def proposal_list(status, category, target, as_json):
    """List proposals (filterable)."""
    store = _store()
    items = store.filter(status=status, category=category, target=target)
    if as_json:
        click.echo(json.dumps(
            [
                {
                    "id": p.id,
                    "status": p.status,
                    "category": p.category,
                    "target": p.target,
                    "title": p.title,
                    "votes": len(p.votes),
                }
                for p in items
            ],
            indent=2,
        ))
        return
    for p in items:
        click.echo(
            f"{p.id}  [{p.status:9s}]  {p.category:7s}  "
            f"votes={len(p.votes):2d}  {p.target}  {p.title}"
        )


@proposal.command("show")
@click.argument("prop_id")
def proposal_show(prop_id: str):
    """Show full proposal YAML."""
    store = _store()
    p = store.path(prop_id)
    if not p.exists():
        raise click.ClickException(f"{prop_id} not found at {p}")
    click.echo(p.read_text())


@proposal.command("create")
@click.option("--title", required=True)
@click.option(
    "--category",
    type=click.Choice(["rule", "skill", "code", "process", "doc"]),
    required=True,
)
@click.option("--target", required=True)
@click.option("--description", required=True)
@click.option("--rationale", required=True)
@click.option(
    "--related-hypotheses",
    "related",
    default="",
    help="Comma-separated HYP-NNN refs",
)
@click.option("--session", "session_id", required=True, help="Source session id")
@click.option(
    "--failed-session-id",
    default=None,
    help="Set only for meta-proposals (category=process, target=reflect-session)",
)
@click.option("--json", "as_json", is_flag=True)
def proposal_create(
    title, category, target, description, rationale,
    related, session_id, failed_session_id, as_json,
):
    """Create new PROP-NNN. Auto-id."""
    store = _store()
    new_id = next_proposal_id(store.dir)
    related_list = [r.strip() for r in related.split(",") if r.strip()]
    p = Proposal(
        id=new_id,
        created=datetime.now(tz=timezone.utc),
        title=title,
        category=category,
        target=target,
        description=description,
        rationale=rationale,
        related_hypotheses=related_list,
        failed_session_id=failed_session_id,
    )
    store.create(p)
    if as_json:
        click.echo(json.dumps({"id": new_id, "session": session_id}))
    else:
        console.print(f"[green]✓[/green] Created {new_id} ({category}/{target})")


@proposal.command("vote")
@click.option("--prop", "prop_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--reasoning", required=True, help="One-line reasoning")
def proposal_vote(prop_id, session_id, reasoning):
    """+1 vote on existing proposal."""
    store = _store()
    if not store.path(prop_id).exists():
        raise click.ClickException(f"{prop_id} not found")
    v = Vote(
        session_id=session_id,
        judge_session_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        reasoning=reasoning,
    )
    p = store.add_vote(prop_id, v)
    console.print(
        f"[green]✓[/green] {prop_id}: vote added ({len(p.votes)} total)"
    )


@proposal.command("status")
@click.option("--prop", "prop_id", required=True)
@click.option(
    "--status",
    type=click.Choice(["open", "accepted", "rejected", "deferred", "duplicate"]),
    required=True,
)
def proposal_status(prop_id, status):
    """Update proposal status (used by `reflect-all commit`)."""
    store = _store()
    if not store.path(prop_id).exists():
        raise click.ClickException(f"{prop_id} not found")
    p = store.set_status(prop_id, status)
    console.print(f"[green]✓[/green] {prop_id}: status={p.status}")
