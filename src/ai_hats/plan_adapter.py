"""``CompositionResult`` → the plan's composition half (ADR-0036 D7).

The tail of today's path: the composer is untouched, and this is the one stage
that reads the library — payloads, rule bodies, trait configs, tree digests.
Everything downstream of the value it returns is shared with the new DSL.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult

from .diagnostics import Level
from .fs_digest import dir_digest
from .plan import (
    CompositionPlan,
    Consent,
    Diagnostic,
    GitHook,
    Hooks,
    OnError,
    Prompt,
    PromptMember,
    RuntimeHook,
    SkillSource,
    Sources,
    TraceEntry,
    WorkflowHook,
    WorktreeHook,
)

if TYPE_CHECKING:
    from .config.overlay import OverlayConfig
    from .resolver import LibraryResolver


_OVERRIDE_NS = "overrides"
_RUNTIME_LAYER = "runtime"


class AdaptError(RuntimeError):
    """The composition result broke an invariant the adapter relies on."""


def adapt(
    result: CompositionResult,
    *,
    identity: str,
    resolver: LibraryResolver,
    overlays: Sequence[tuple[OverlayConfig, str]],
) -> tuple[CompositionPlan, Sources]:
    """Today's composition as the plan's composition half, plus where its bytes live.

    ``overlays`` are exactly the layers the composer applied, each with its
    label (``global`` / ``project`` / ``runtime``): they name the text a layer
    appended and the layer that brought or removed a term. A layer left out is
    an injection the adapter cannot name — a refusal, not a guess.
    """
    from .surfaces import compose_sections

    diagnostics: list[Diagnostic] = [
        Diagnostic(Level.WARN, err.message) for err in result.errors if not err.lossy
    ]
    return (
        CompositionPlan(
            identity=identity,
            prompt=Prompt(
                text=compose_sections(result),
                members=_prompt_members(result, overlays),
            ),
            skills=tuple(_skill_name(s.name) for s in result.skills),
            hooks=_hooks(result, diagnostics),
            consent=_consent(result),
            trace=_trace(result, identity, resolver, overlays, diagnostics),
            diagnostics=tuple(diagnostics),
        ),
        Sources(
            skills={
                _skill_name(s.name): SkillSource(
                    path=s.source_path, digest=dir_digest(s.source_path)
                )
                for s in result.skills
            }
        ),
    )


def _skill_name(name: str) -> str:
    return f"skills::{name}"


def _rule_name(name: str) -> str:
    return f"rules::{name}"


def _payload(skill_name: str, script: str) -> str:
    from .libraries.models import resolve_namespace

    return f"skills/{resolve_namespace(skill_name)}/{script}"


def _override_name(label: str, identity: str) -> str:
    return identity if label == _RUNTIME_LAYER else f"{_OVERRIDE_NS}::{label}"


def _prompt_members(
    result: CompositionResult, overlays: Sequence[tuple[OverlayConfig, str]]
) -> tuple[PromptMember, ...]:
    """The ``(block, name)`` pairs ``compose_sections`` assembled, in its order."""
    from .resolver import read_rule_body

    members: list[PromptMember] = []
    if result.priorities:
        members.append(PromptMember("PRIORITIES", f"{result.name}::priorities"))

    named: dict[str, str] = {}
    for trait, text in result.trait_injections.items():
        named.setdefault(text, f"{trait}::prompt")
    if result.role_injection:
        named.setdefault(result.role_injection, f"{result.name}::prompt")
    for layer, label in overlays:
        text = layer.injection_append.strip()
        if text:
            named.setdefault(text, f"{_OVERRIDE_NS}::{label}::prompt")
    for text in result.injections:
        if not text.strip():
            continue
        if text not in named:
            head = text.splitlines()[0][:80]
            raise AdaptError(
                f"an injection reached the prompt that no role, trait or overlay declared "
                f"(starts {head!r}); pass every layer the composer applied"
            )
        members.append(PromptMember("body", named[text]))

    for rule in result.rules:
        if rule.source_path and read_rule_body(rule.source_path):
            members.append(PromptMember("RULES", _rule_name(rule.name)))

    for path in result.user_rules:
        try:
            body = path.read_text()
        except OSError:
            continue  # compose_sections skips it too, with its own warning
        if body.strip():
            members.append(PromptMember("USER RULES", _rule_name(path.stem)))
    return tuple(members)


def _hooks(result: CompositionResult, diagnostics: list[Diagnostic]) -> Hooks:
    """The three frontmatter channels and the check bindings, one hook per point.

    A declared payload that is missing or not executable is the author's to fix:
    it becomes a diagnostic and no hook, on every channel alike.
    """
    from ai_hats_wt import parse_worktree_carry

    from .libraries.models import SkillMetadata

    git: list[GitHook] = []
    runtime: list[RuntimeHook] = []
    worktree: list[WorktreeHook] = []

    def wired(skill_name: str, script: str, channel: str, at: str) -> bool:
        declared = result_skill_dirs[skill_name] / script
        if not declared.is_file():
            what = f"missing at {declared}"
        elif not os.access(declared, os.X_OK):
            what = f"not executable: {declared}"
        else:
            return True
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"{channel} hook {at} of {_skill_name(skill_name)}: {script} is {what}; "
                "this hook will not run in this session",
            )
        )
        return False

    result_skill_dirs = {s.name: s.source_path for s in result.skills}
    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        for event, scripts in metadata.git_hooks.items():
            for script in scripts:
                if wired(skill.name, script, "git", event):
                    git.append(GitHook(at=event, run=_payload(skill.name, script)))
        for event, hooks in metadata.runtime_hooks.items():
            for hook in hooks:
                if wired(skill.name, hook.script, "runtime", f"{event}/{hook.matcher}"):
                    runtime.append(
                        RuntimeHook(
                            at=event, matcher=hook.matcher, run=_payload(skill.name, hook.script)
                        )
                    )
        carry = parse_worktree_carry(metadata.worktree, skill.name)
        for hook in carry.wt_in:
            if wired(skill.name, hook.script, "worktree", "wt_in"):
                worktree.append(
                    WorktreeHook(at="wt_in", on=None, run=_payload(skill.name, hook.script))
                )
        for hook in carry.wt_out:
            if wired(skill.name, hook.script, "worktree", "wt_out"):
                worktree.append(
                    WorktreeHook(
                        at="wt_out", on=tuple(hook.on), run=_payload(skill.name, hook.script)
                    )
                )

    workflow = tuple(
        WorkflowHook(
            app=check.app,
            object=".".join(check.path) if check.path else None,
            at=at,
            run=_payload(check.skill, check.script),
            on_error=OnError(check.on_error),
        )
        for check in result.checks
        for at in check.at
    )
    return Hooks(
        git=tuple(git), runtime=tuple(runtime), workflow=workflow, worktree=tuple(worktree)
    )


def _consent(result: CompositionResult) -> tuple[Consent, ...]:
    from .check_points import selector_ends

    rows = []
    for point in result.consent:
        source, target = selector_ends(point.app, point.selector, point.path)
        rows.append(
            Consent(
                operation=".".join(point.path),
                at=point.selector,
                source=source,
                target=target,
                declared_by=point.declared_by,
                disarmed_by=None,
            )
        )
    return tuple(rows)


def _trace(
    result: CompositionResult,
    identity: str,
    resolver: LibraryResolver,
    overlays: Sequence[tuple[OverlayConfig, str]],
    diagnostics: list[Diagnostic],
) -> tuple[TraceEntry, ...]:
    """Who brought each term — the composer's own walk, attributed.

    Traits: the role's list, then each layer's removes and adds in order — a
    same-layer remove+add is the reorder the composer performs, and the trace
    shows it as such. Rules and skills: the first trait declaring one holds
    it, then the role, then a layer; a removal takes effect only when the name
    is not in the role's own list once the layers are applied — the composer's
    rule, so a re-added name never leaves.
    """
    role = resolver.resolve_role_config(result.name)
    traits: list[tuple[str, str]] = [
        (t, result.name) for t in (role.composition.traits if role else [])
    ]
    removed: list[TraceEntry] = []
    for layer, label in overlays:
        by = _override_name(label, identity)
        for name in layer.remove_traits:
            for i, (trait, brought_by) in enumerate(traits):
                if trait == name:
                    removed.append(TraceEntry(trait, brought_by, removed_by=by))
                    del traits[i]
                    break
        for name in layer.add_traits:
            if all(trait != name for trait, _ in traits):
                traits.append((name, by))

    brought: dict[str, str] = {}
    for trait, _ in traits:
        cfg = resolver.resolve_trait_config(trait)
        if cfg is None:
            continue
        for rule in cfg.composition.rules:
            brought.setdefault(_rule_name(rule), trait)
        for skill in cfg.composition.skills:
            brought.setdefault(_skill_name(skill), trait)
    own = {_rule_name(r): None for r in (role.composition.rules if role else [])} | {
        _skill_name(s): None for s in (role.composition.skills if role else [])
    }
    for name in own:
        brought.setdefault(name, result.name)
    requested: dict[str, str] = {}
    for layer, label in overlays:
        by = _override_name(label, identity)
        for name in (*map(_rule_name, layer.remove_rules), *map(_skill_name, layer.remove_skills)):
            own.pop(name, None)
            requested[name] = by
        for name in (*map(_rule_name, layer.add_rules), *map(_skill_name, layer.add_skills)):
            own[name] = None
            brought.setdefault(name, by)
    for name, by in requested.items():
        if name not in own and name in brought:
            removed.append(TraceEntry(name, brought.pop(name), removed_by=by))

    present = [TraceEntry(trait, by, removed_by=None) for trait, by in traits]
    for name in (
        *map(_rule_name, (r.name for r in result.rules)),
        *(_skill_name(s.name) for s in result.skills),
    ):
        if name not in brought:
            diagnostics.append(
                Diagnostic(
                    Level.WARN,
                    f"{name} composed, but no role, trait or overlay declares it; "
                    f"attributed to the expression",
                )
            )
        present.append(TraceEntry(name, brought.get(name, identity), removed_by=None))
    return (*present, *removed)
