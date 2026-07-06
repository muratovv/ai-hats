"""Shared assembly of a ticket's linked-task context.

HATS-689 introduced the ``LINKED_CONTEXT`` block for the Automate sub-agent
prompt (``SubAgentRunner``). HATS-691 reuses the very same assembly for
``ai-hats task show`` so a human (and the interactive agent, which reads its
task via ``task show``) sees the same content the sub-agent gets. The logic
therefore lives here as module-level functions — a single "what context does a
task see" path, the seam HATS-558 later extends — rather than as methods on one
runner.

Direct links only; one level; no recursion / transitive walk. Every reader is
graceful on missing targets (skip, never raise).
"""

from __future__ import annotations

from pathlib import Path

from .models import TaskCard


def load_ticket(*, tasks_root: Path, ticket_id: str) -> str:
    """Return the raw ``task.yaml`` text for a ticket (``""`` if absent).

    ``tasks_root`` is injected integrator policy (``paths.tasks_dir``, HATS-864);
    keyword-only so a ``project_dir`` can never silently slot in (both are Path
    and this module degrades gracefully instead of raising).
    """
    task_file = tasks_root / ticket_id / "task.yaml"
    if task_file.exists():
        return task_file.read_text()
    return ""


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
    except Exception:
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
        except Exception:
            continue
        blocks.append(render_linked_card(kind, linked, base))
    return "\n\n".join(blocks)


def render_linked_card(kind: str, card: TaskCard, base: Path) -> str:
    """Render one trimmed linked-card block (see :func:`load_linked_context`)."""
    lines = [f"## {card.id} — {card.title}  [{kind}]", f"state: {card.state.value}"]
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
