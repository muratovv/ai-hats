"""The plan path for tests that used to drive a surface's builder: compose →
adapt → plan → apply, in the shape every runner takes (ADR-0036 D2–D4)."""

from __future__ import annotations

from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.fs_digest import dir_digest
from ai_hats.resolver import LibraryResolver
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import plan_session, probe_host
from ai_hats.surfaces import Surface, adapt, apply
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Hooks,
    LaunchFlags,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
    Skill,
)

_ON_PATH = ("scripts", "bin")


def composition_of(
    result, *, layout: ProjectLayout, resolver=None, identity=None
) -> CompositionPlan:
    """Today's composition as the plan's composition half; a synthetic result
    needs no library, so the resolver may be empty."""
    return adapt(
        result,
        identity=identity or result.name,
        layout=layout,
        resolver=resolver or LibraryResolver([]),
        overlays=(),
        diagnostics=[],
    )


def skill_of(
    skill_dir: Path, *, layout: ProjectLayout | None = None, name: str | None = None
) -> Skill:
    """A composed skill over a tree on disk — its document rendered for the
    layout the way the adapter renders it, or the raw ``SKILL.md`` without one."""
    from ai_hats.surfaces.plan_adapter import _document

    source = skill_dir / "SKILL.md"
    if layout is not None:
        document = _document(skill_dir, layout)
    else:
        document = source.read_text() if source.is_file() else None
    return Skill(
        name=f"skills::{name or skill_dir.name}",
        path=skill_dir.resolve(),
        content_digest=dir_digest(skill_dir),
        document=document,
        on_path=tuple(d for d in _ON_PATH if (skill_dir / d).is_dir()),
    )


def composition_with(identity: str, skills=(), *, prompt: str = "# r\n") -> CompositionPlan:
    """A composition of one prose member and the given skills — no hooks, no trace."""
    return CompositionPlan(
        identity=identity,
        prompt=Prompt((PromptBlock(None, (PromptMember(f"{identity}::prompt", prompt, None),)),)),
        skills=tuple(skills),
        hooks=Hooks((), ()),
        trace=(),
    )


def planned(
    surface: Surface,
    composition: CompositionPlan,
    *,
    layout: ProjectLayout,
    root: Path,
    run_mode: RunMode = RunMode.HITL,
    policy: SessionPolicy | None = None,
    middleware: bool = True,
) -> MaterializationPlan:
    """The surface's plan as the runners take it; ``middleware=False`` is the
    surface's own plan without the consent layer, for a surface that cannot
    carry the role's consent (agy) on a role that declares it."""
    given = dict(run_mode=run_mode, policy=policy or SessionPolicy(), root=root, layout=layout)
    host = probe_host(surface=surface)
    if not middleware:
        return surface.plan(composition, host=host, **given)
    return plan_session(composition, surface, host=host, **given)


def materialized(
    surface: Surface, composition: CompositionPlan, *, layout: ProjectLayout, root: Path, **kw
):
    """Plan and apply — the session on disk, and the plan it was made from."""
    plan = planned(surface, composition, layout=layout, root=root, **kw)
    apply(plan)
    return plan


def automate_prompt(
    surface: Surface,
    composition: CompositionPlan,
    *,
    layout: ProjectLayout,
    root: Path,
    task: str = "",
    ticket_id: str = "",
) -> str:
    """The bytes the AUTOMATE path renders into ``meta_prompt.txt`` — the
    launch's own prompt, not a test-only builder (HATS-1552)."""
    from ai_hats.session_artifacts import assemble_brief

    plan = planned(surface, composition, layout=layout, root=root, run_mode=RunMode.AUTOMATE)
    brief = assemble_brief(layout, task=task, ticket_id=ticket_id)
    return surface.automate_launch(plan, flags(root, brief=brief), {}, layout=layout).prompt


def flags(root: Path, **overrides) -> LaunchFlags:
    given = dict(
        session_id=root.name,
        session_dir=root,
        trace_path="t",
        root_pid="1",
        provider_session_id="u",
    )
    given.update(overrides)
    return LaunchFlags(**given)
