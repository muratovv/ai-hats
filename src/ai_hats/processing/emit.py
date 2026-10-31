"""Emission (ADR-0035 D9): the plan's composition half, and nothing else.

Texts are expanded for the project layout here, as the adapter expands them,
so the prompt and the skill documents are the bytes the agent reads.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from ..diagnostics import Diagnostic
from ..surfaces import CompositionPlan, Hooks, Skill
from .errors import ProcessingError
from .fold import Folded
from .names import Tree
from .parse import Declared, SkillEntity
from .prompt import assemble


def emit(
    folded: Folded,
    tree: Tree,
    *,
    identity: str,
    layout: ProjectLayout,
    diagnostics: list[Diagnostic],
) -> CompositionPlan:
    from ..placeholders import expand_fsm_edges_token, expand_path_placeholders

    def member_text(text: str) -> str:
        return expand_path_placeholders(text, layout)

    def document(text: str) -> str:
        return expand_fsm_edges_token(expand_path_placeholders(text, layout), layout)

    skills: list[Skill] = []
    for name in folded.members:
        entity = tree.entities[name]
        if isinstance(entity, SkillEntity):
            skills.append(
                Skill(
                    name=name,
                    path=entity.path.resolve(),
                    content_digest=entity.content_digest,
                    document=document(entity.document),
                    on_path=entity.on_path,
                )
            )
        elif isinstance(entity, Declared):
            raise ProcessingError(
                f"{name} is a {entity.type}: hooks reach the plan in a later slice"
            )
    return CompositionPlan(
        identity=identity,
        prompt=assemble(folded, tree, diagnostics, expand=member_text),
        skills=tuple(skills),
        hooks=Hooks(runtime=(), external=()),
        trace=folded.trace,
    )
