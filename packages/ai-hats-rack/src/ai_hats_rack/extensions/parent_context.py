"""Parent-context read enricher (HATS-1064): on a context read of a child, walk
the parent chain and contribute each ancestor's card body — the parents' carried
governance (the epic's "Requirements for child tasks" section) — so pickup
delivers it mechanically instead of relying on the agent reading the parent.

READ-phase, read-only: contributes a :class:`ReadContribution`, never a Delta.
Distinct from ``epic-automation`` (transition-phase, writes the epic) — the two
faces of the ``parent_task`` relationship; they share only the ``parent_of``
primitive on the registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Sequence

from ..dispatch import DispatchContext, ReadContribution
from ..registry import LinksRegistry

if TYPE_CHECKING:
    from ..kernel import Kernel
    from ..models import TaskCard

#: Backstop on the upward walk — a parent_task cycle is constructible in the data
#: (``set_parent`` only rejects self-parenting), so the walk is also cycle-guarded.
MAX_DEPTH = 64


def walk_parent_chain(
    start: TaskCard,
    parent_id_of: Callable[[TaskCard], str],
    get_card: Callable[[str], TaskCard | None],
    *,
    max_depth: int = MAX_DEPTH,
) -> list[TaskCard]:
    """The pure, table-testable walk: ancestors of ``start``, nearest first.

    Cycle-guarded (revisit → stop) and depth-capped, and it stops on a dangling
    id (``get_card`` → ``None``) — so a broken or cyclic chain can never hang a
    read. ``parent_id_of`` returns ``""`` when there is no parent.
    """
    chain: list[TaskCard] = []
    visited = {start.id}
    current = start
    while len(chain) < max_depth:
        parent_id = parent_id_of(current)
        if not parent_id or parent_id in visited:
            break
        visited.add(parent_id)
        parent = get_card(parent_id)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    return chain


def render_chain(chain: Sequence[TaskCard]) -> str:
    """One block per ancestor (nearest first): ``id [state] title`` + body."""
    blocks = []
    for card in chain:
        head = f"{card.id} [{card.state}] {card.title}".rstrip()
        body = (card.description or "").strip()
        blocks.append(f"{head}\n{body}" if body else head)
    return "\n\n".join(blocks)


class ParentContextExtension:
    """READ subscriber bound (via ``links.kinds[].read``) to the hierarchy kind:
    on a read of a card carrying that link, contribute the parent chain's bodies.
    A :class:`BindableSubscriber` — the read path binds it to the read kernel so
    it can resolve ancestors."""

    name = "parent-context"

    def __init__(self, registry: LinksRegistry) -> None:
        self._registry = registry
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        self._kernel = kernel

    def on_read(self, ctx: DispatchContext) -> ReadContribution | None:
        if self._kernel is None:
            raise RuntimeError("parent-context is not bound to a kernel (call bind())")
        chain = walk_parent_chain(ctx.task, self._registry.parent_of, self._kernel.get)
        if not chain:
            return None
        return ReadContribution(self.name, render_chain(chain))
