"""Prompt assembly (ADR-0035 D5): a prompt term joins the tail of its own
block, a block opens where its first member appears, ``body`` renders bare."""

from __future__ import annotations

from collections.abc import Callable

from ..diagnostics import Diagnostic, Level
from ..surfaces import Prompt, PromptBlock, PromptMember
from .fold import Folded
from .names import Tree
from .parse import PromptEntity

BODY = "body"


def assemble(
    folded: Folded,
    tree: Tree,
    diagnostics: list[Diagnostic],
    *,
    expand: Callable[[str], str] = lambda text: text,
) -> Prompt:
    """``expand`` renders each member's text for the project the agent reads
    it in (placeholders); the blocks and their order are the value."""
    blocks: dict[str | None, list[PromptMember]] = {}
    first_body: str | None = None
    for name in folded.members:
        entity = tree.entities[name]
        if not isinstance(entity, PromptEntity):
            continue
        block = None if entity.block == BODY else entity.block
        if block is None and first_body is None:
            first_body = name
        blocks.setdefault(block, []).append(
            PromptMember(name=name, text=expand(entity.text), heading=entity.heading)
        )
    if first_body is not None and folded.first_top_index[first_body] != 0:
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"the first body prompt, {first_body}, comes through term "
                f"{folded.first_top_index[first_body] + 1} of the expression, not the first — "
                "the prose the agent reads first is not the first term's",
            )
        )
    return Prompt(
        blocks=tuple(PromptBlock(name, tuple(members)) for name, members in blocks.items())
    )
