"""Parent-context read enricher (HATS-1064; field-based since HATS-1067): on a
context read of a child, walk the parent chain and contribute each ancestor's
``work_policy`` field — the work policy a card carries for itself and its
children. ONLY that field travels, never the whole parent card, so the read
stays tight; an ancestor with an empty ``work_policy`` contributes nothing.

READ-phase, read-only: contributes a :class:`ReadContribution`, never a Delta.
Distinct from ``epic-automation`` (transition-phase, writes the epic) — the two
faces of the ``parent_task`` relationship; they share only ``parent_of``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Sequence

from ..dispatch import DispatchContext, ReadContribution
from ..registry import LinksRegistry

if TYPE_CHECKING:
    from ..kernel import Kernel
    from ..models import TaskCard

#: Depth backstop on the upward walk (also cycle-guarded): a parent_task cycle is
#: constructible in the data (``set_parent`` only rejects self-parenting).
MAX_DEPTH = 64


def walk_parent_chain(
    start: TaskCard,
    parent_id_of: Callable[[TaskCard], str],
    get_card: Callable[[str], TaskCard | None],
    *,
    max_depth: int = MAX_DEPTH,
) -> tuple[list[TaskCard], str]:
    """The pure, table-testable walk: ``(ancestors nearest-first, note)``.

    Cycle-/depth-/dangling-guarded so a broken chain can never hang a read. A
    closed cycle or a hit depth cap means the ``parent_task`` hierarchy is
    inconsistent (a hierarchy must be acyclic) — reported in ``note`` for the
    agent, not silently swallowed. A dangling id stops the walk quietly (a parent
    may legitimately be absent — e.g. mid-migration). ``parent_id_of`` returns
    ``""`` when there is no parent.
    """
    chain: list[TaskCard] = []
    visited = {start.id}
    current = start
    note = ""
    while True:
        if len(chain) >= max_depth:
            note = f"parent chain exceeds {max_depth} levels — possible parent_task cycle; truncated"
            break
        parent_id = parent_id_of(current)
        if not parent_id:
            break
        if parent_id in visited:
            note = (
                f"parent_task cycle at {parent_id!r} — the parent hierarchy is inconsistent "
                f"(a card is its own ancestor); chain truncated here"
            )
            break
        visited.add(parent_id)
        parent = get_card(parent_id)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    return chain, note


def render_chain(chain: Sequence[TaskCard]) -> str:
    """One block per ancestor that CARRIES a ``work_policy`` (nearest first):
    ``id [state] title`` + the policy body. Ancestors with an empty field are
    skipped — only governance travels, not the whole card."""
    blocks = []
    for card in chain:
        policy = (card.work_policy or "").strip()
        if not policy:
            continue
        head = f"{card.id} [{card.state}] {card.title}".rstrip()
        blocks.append(f"{head}\n{policy}")
    return "\n\n".join(blocks)


class ParentContextExtension:
    """READ subscriber bound (via ``links.kinds[].read``) to the hierarchy kind:
    on a read of a card carrying that link, contribute the parent chain's
    ``work_policy`` fields. A :class:`BindableSubscriber` — the read path binds
    it to the read kernel so it can resolve ancestors."""

    name = "parent-context"

    def __init__(self, registry: LinksRegistry) -> None:
        self._registry = registry
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        self._kernel = kernel

    def on_read(self, ctx: DispatchContext) -> ReadContribution | None:
        if self._kernel is None:
            raise RuntimeError("parent-context is not bound to a kernel (call bind())")
        chain, note = walk_parent_chain(ctx.task, self._registry.parent_of, self._kernel.get)
        body = render_chain(chain)
        if note:  # surface the inconsistency to the agent, above any governance
            body = f"⚠ {note}\n\n{body}".rstrip() if body else f"⚠ {note}"
        return ReadContribution(self.name, body) if body else None
