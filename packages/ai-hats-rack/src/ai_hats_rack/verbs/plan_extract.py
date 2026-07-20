"""``rack plan-extract`` — read a card's plan.md and batch-create child cards
from its structured sections (HATS-1054 R4).

A COMPOSITE (read the doc → create N children → parent-link each): not the single
mutating ``transition`` (one card's state) nor ``create`` (one card), so it is a
top-level verb beside ``create``, on the tasks backlog. Machine-first: no
per-candidate prompt (tracker's interactive port collapses to create-all);
``--dry-run`` previews. Idempotent — each processed line is stamped
``<!-- <child-id> -->`` so re-runs skip it.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, handle_rack_error
from ..cli_kernel import _build_kernel, _provider
from ..docstore import DocStore
from ..kernel import UnknownTaskError
from ..models import atomic_write_text
from ..plan_extract import extract_candidates, mark_extracted
from . import Verb

_EXTRACT_TAG = "extracted-from-plan"


@click.command("plan-extract")
@click.argument("task_id")
@click.option("--dry-run", is_flag=True, help="List candidates without creating cards.")
@TASKS_DIR_OPT
@JSON_OPT
def plan_extract(task_id: str, dry_run: bool, tasks_dir: Path | None, as_json: bool) -> None:
    """Create child cards from plan.md sections (Subtasks/Steps/numbered headings).

    Each child is parented to TASK_ID, tagged ``extracted-from-plan``, and its
    source line stamped for idempotency. Candidates are found in priority order:
    ``## Subtasks`` bullets, then ``## Steps`` checklist, then numbered
    ``### N. …`` / ``### Phase|Step N: …`` headings.
    """
    caller_cwd = Path.cwd()
    provider = _provider()
    try:
        kernel, root = _build_kernel(tasks_dir, caller_cwd, provider)
        card_dir = DocStore(root.tasks_dir).card_dir(task_id)
        if not (card_dir / "task.yaml").exists():
            raise UnknownTaskError(task_id)
        plan_path = card_dir / "plan.md"
        if not plan_path.is_file():
            raise UnknownTaskError(task_id)  # no plan document to extract from
        plan_text = plan_path.read_text()
        candidates = extract_candidates(plan_text)

        if dry_run:
            _emit_candidates(candidates, as_json)
            return

        text = plan_text
        created: list[tuple[str, str]] = []
        for cand in candidates:
            result = kernel.create(
                actor=actor(),
                caller_cwd=caller_cwd,
                title=cand.title,
                priority="medium",
                tags=[_EXTRACT_TAG],
                parent_task=task_id,
            )
            child_id = result.task.id
            text = mark_extracted(text, cand.line_no, child_id)
            created.append((child_id, cand.title))
        if created:
            atomic_write_text(plan_path, text)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return

    _emit_created(task_id, created, as_json)


def _emit_candidates(candidates, as_json: bool) -> None:
    if as_json:
        emit_json({"candidates": [c.to_dict() for c in candidates]})
        return
    if not candidates:
        click.echo("No candidates found.")
        return
    for c in candidates:
        click.echo(f"[{c.kind}] line {c.line_no}: {c.title}")


def _emit_created(task_id: str, created: list[tuple[str, str]], as_json: bool) -> None:
    if as_json:
        emit_json(
            {"task_id": task_id, "created": [{"id": cid, "title": t} for cid, t in created]}
        )
        return
    if not created:
        click.echo("No candidates found — nothing extracted.")
        return
    click.echo(f"Created {len(created)} child card(s):")
    for cid, title in created:
        click.echo(f"  {cid}: {title}")


def verb() -> Verb:
    return Verb("plan-extract", lambda defn: plan_extract)
