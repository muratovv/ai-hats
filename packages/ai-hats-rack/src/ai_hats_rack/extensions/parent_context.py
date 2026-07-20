"""Parent-context read enricher (HATS-1064): on a context read of a child, walk
the parent chain and contribute each ancestor's "Work Policy" section (PROP-086)
— the work policy a card carries for itself and its children. ONLY that section
travels, never the whole parent card, so the read stays tight; an ancestor
without the section contributes nothing.

READ-phase, read-only: contributes a :class:`ReadContribution`, never a Delta.
Distinct from ``epic-automation`` (transition-phase, writes the epic) — the two
faces of the ``parent_task`` relationship; they share only ``parent_of``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Sequence

from ..dispatch import DispatchContext, ReadContribution
from ..registry import LinksRegistry

if TYPE_CHECKING:
    from ..kernel import Kernel
    from ..models import TaskCard

#: Depth backstop on the upward walk (also cycle-guarded): a parent_task cycle is
#: constructible in the data (``set_parent`` only rejects self-parenting).
MAX_DEPTH = 64

#: The work-policy section a card carries for itself and its children (PROP-086,
#: renamed "Work Policy"); overridable per handler via the ``work_policy`` config
#: key on ``kinds[].read``.
DEFAULT_SECTION = "Work Policy"

_HEADING = re.compile(r"^(#+)\s+(.*\S)\s*$")


def extract_section(text: str, title: str) -> str:
    """Body of the markdown section whose heading equals ``title`` (any ``#``
    level), up to the next same-or-higher heading; ``""`` when absent."""
    out: list[str] = []
    capturing = False
    level = 0
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            depth, heading = len(m.group(1)), m.group(2).strip()
            if not capturing and heading.casefold() == title.casefold():
                capturing, level = True, depth
                continue
            if capturing and depth <= level:
                break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


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


def render_chain(chain: Sequence[TaskCard], section_title: str) -> str:
    """One block per ancestor that CARRIES the section (nearest first):
    ``id [state] title`` + the section body. Ancestors without it are skipped —
    only governance travels, not the whole card."""
    blocks = []
    for card in chain:
        section = extract_section(card.description or "", section_title)
        if not section:
            continue
        head = f"{card.id} [{card.state}] {card.title}".rstrip()
        blocks.append(f"{head}\n{section}")
    return "\n\n".join(blocks)


class ParentContextExtension:
    """READ subscriber bound (via ``links.kinds[].read``) to the hierarchy kind:
    on a read of a card carrying that link, contribute the parent chain's
    "Work Policy" sections. A :class:`BindableSubscriber` — the
    read path binds it to the read kernel so it can resolve ancestors."""

    name = "parent-context"

    def __init__(self, registry: LinksRegistry, *, section: str | None = None) -> None:
        self._registry = registry
        self._section = section or DEFAULT_SECTION
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        self._kernel = kernel

    def on_read(self, ctx: DispatchContext) -> ReadContribution | None:
        if self._kernel is None:
            raise RuntimeError("parent-context is not bound to a kernel (call bind())")
        chain, note = walk_parent_chain(ctx.task, self._registry.parent_of, self._kernel.get)
        body = render_chain(chain, self._section)
        if note:  # surface the inconsistency to the agent, above any governance
            body = f"⚠ {note}\n\n{body}".rstrip() if body else f"⚠ {note}"
        return ReadContribution(self.name, body) if body else None
