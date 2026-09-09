"""Assembly of a ticket's linked-task context for the ``LINKED_CONTEXT`` block.

Module-level functions rather than ``SubAgentRunner`` methods: one "what context
does a task see" path (HATS-689; the seam HATS-558 extends). Cards are read with
the rack model — same on-disk ``task.yaml``, no tracker dependency (HATS-1258).

Direct links only; one level; no recursion / transitive walk. Every reader is
graceful on missing targets (skip, never raise).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai_hats_rack.models import TaskCard

logger = logging.getLogger(__name__)


def load_ticket(*, tasks_root: Path, ticket_id: str) -> str:
    """Return the raw ``task.yaml`` text for a ticket (``""`` if absent).

    ``tasks_root`` is injected integrator policy (``TrackerLayout.tasks_dir``, HATS-864);
    keyword-only so a ``project_dir`` can never silently slot in (both are Path
    and this module degrades gracefully instead of raising).
    """
    task_file = tasks_root / ticket_id / "task.yaml"
    if task_file.exists():
        return task_file.read_text()
    return ""


def ticket_sections(*, tasks_root: Path, ticket_id: str) -> tuple[str, str]:
    """``(ticket_context, linked_context)`` for a ticket — ``("", "")`` when absent.

    The pair travels together into every prompt that carries either, so one call
    site keeps a caller from taking the card and forgetting its links (HATS-1552).
    """
    if not ticket_id:
        return "", ""
    return (
        load_ticket(tasks_root=tasks_root, ticket_id=ticket_id),
        load_linked_context(tasks_root=tasks_root, ticket_id=ticket_id),
    )


def load_linked_context(*, tasks_root: Path, ticket_id: str) -> str:
    """Assemble the linked-context body for a ticket's direct links.

    Links are pulled in salience order ``parent_task → depends_on → related →
    see_also`` (deduped; self and missing targets skipped). Per linked card: a
    trimmed view (id, title, state, description) plus only the *latest*
    ``work_log`` entry (token hygiene — the argument that dropped
    ``PROJECT_STATE`` in HATS-681). The parent epic additionally carries its
    ``plan.md`` body; other links are card-only.

    Returns ``""`` when there are no resolvable links (the caller skips the
    section).
    """
    if not ticket_id:
        return ""
    base = tasks_root
    card_path = base / ticket_id / "task.yaml"
    if not card_path.exists():
        return ""
    try:
        card = TaskCard.from_yaml(card_path)
    except Exception as exc:
        # The subject's OWN card, unlike the neighbours skipped below: folding a
        # corrupt one into "" hands the agent a prompt with no epic, no
        # depends_on and no plan, indistinguishable from a card with no links
        # (HATS-1373). The module contract forbids raising, so it must be loud.
        logger.error("linked context: subject card %s did not load: %r", card_path, exc)
        return ""

    # Salience order, parent first; dedup on id, never pull self.
    ordered: list[tuple[str, str]] = []
    seen: set[str] = {ticket_id}

    def _add(kind: str, ids: list[str]) -> None:
        for lid in ids:
            if lid and lid not in seen:
                seen.add(lid)
                ordered.append((kind, lid))

    if card.parent_task:
        _add("parent_task", [card.parent_task])
    _add("depends_on", card.depends_on)
    _add("related", card.related)
    _add("see_also", card.see_also)

    blocks: list[str] = []
    for kind, lid in ordered:
        linked_path = base / lid / "task.yaml"
        if not linked_path.exists():
            continue  # graceful: dangling link, skip
        try:
            linked = TaskCard.from_yaml(linked_path)
        except Exception:  # noqa: S112
            # silent-ok: a dangling link target is skipped, as in the branch above
            continue
        blocks.append(render_linked_card(kind, linked, base))
    return "\n\n".join(blocks)


def render_linked_card(kind: str, card: TaskCard, base: Path) -> str:
    """Render one trimmed linked-card block (see :func:`load_linked_context`)."""
    lines = [f"## {card.id} — {card.title}  [{kind}]", f"state: {card.state}"]
    if card.description:
        lines.append("")
        lines.append(card.description.rstrip())
    if card.work_log:
        latest = card.work_log[-1]
        ts = latest.timestamp or "?"
        lines.append("")
        lines.append(f"latest work_log ({ts}): {latest.message}")
    block = "\n".join(lines)
    # The parent epic additionally carries its plan.md (design lives there).
    if kind == "parent_task":
        plan_path = base / card.id / "plan.md"
        if plan_path.exists():
            plan_body = plan_path.read_text().rstrip()
            if plan_body:
                block += f"\n\n### {card.id} plan.md\n{plan_body}"
    return block
