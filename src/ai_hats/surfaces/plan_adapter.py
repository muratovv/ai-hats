"""``CompositionResult`` → the plan's composition half (ADR-0036 D7).

The tail of today's path: the composer is untouched, and this is the one stage
that reads the library — payloads, rule bodies, trait configs, tree digests.
Everything downstream of the value it returns is shared with the new DSL.

Lifetime: exactly as long as ``-r`` — the new processing emits the composition
half itself (ADR-0036 D8), so this module retires with the composer at the
cutover (ADR-0033 D5, stage 3). Nothing may grow a second consumer of it.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult

from ..diagnostics import Diagnostic, Level
from ..fs_digest import dir_digest
from .hook_channel import HookEvent
from .plan import (
    CompositionPlan,
    Executable,
    ExternalHook,
    Hooks,
    OnError,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
    TraceEntry,
    home_of,
)

if TYPE_CHECKING:
    from ai_hats_core.layout import ProjectLayout

    from ..config.overlay import OverlayConfig
    from ..resolver import LibraryResolver


_OVERRIDE_NS = "overrides"
_RUNTIME_LAYER = "runtime"
#: In PATH order: an earlier skill's script shadows a later one's of the same name.
_ON_PATH = ("scripts", "bin")


class AdaptError(RuntimeError):
    """The composition result broke an invariant the adapter relies on."""


def adapt(
    result: CompositionResult,
    *,
    identity: str,
    layout: ProjectLayout,
    resolver: LibraryResolver,
    overlays: Sequence[tuple[OverlayConfig, str]],
    diagnostics: list[Diagnostic],
) -> CompositionPlan:
    """Today's composition as the plan's composition half.

    ``layout``: what every member's text is expanded for, so the prompt is the
    text the agent reads, not the text as authored. ``overlays``: exactly the
    layers the composer applied, each with its label (``global`` / ``project``
    / ``runtime``) — a layer left out is an injection the adapter cannot name,
    a refusal, not a guess. ``diagnostics``: the composer's own sink; what the
    adapter finds lands beside what the composer found, never in the plan.
    """
    from ..skills_dir import find_skill_script_collisions

    for err in result.errors:
        if not err.lossy:
            diagnostics.append(Diagnostic(Level.WARN, err.message))
    for collision in find_skill_script_collisions(result.skills):
        diagnostics.append(Diagnostic(Level.WARN, collision))
    skills = tuple(
        Skill(
            name=_skill_name(s.name),
            path=s.source_path.resolve(),
            content_digest=dir_digest(s.source_path),
            document=_document(s.source_path, layout),
            on_path=tuple(d for d in _ON_PATH if (s.source_path / d).is_dir()),
        )
        for s in result.skills
    )
    hooks = _hooks(result, diagnostics)
    _check_diagnostics(skills, hooks, diagnostics)
    return CompositionPlan(
        identity=identity,
        prompt=Prompt(blocks=_prompt_blocks(result, overlays, layout)),
        skills=skills,
        hooks=hooks,
        trace=_trace(result, identity, resolver, overlays, diagnostics),
    )


def _check_diagnostics(
    skills: tuple[Skill, ...], hooks: Hooks, diagnostics: list[Diagnostic]
) -> None:
    """A check binding whose script lies outside every composed skill, said
    here where the library is read: no session mirror will hold its bytes."""
    outside: dict[Path, str] = {}
    for hook in hooks.external:
        if hook.on_error is None or hook.run is None or home_of(hook.run, skills) is not None:
            continue
        outside.setdefault(hook.run.path, hook.declared_by)
    for path, by in outside.items():
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"check of {by} runs {path}, outside every composed skill; the session "
                "mirror holds no bytes for it and the gate will not run in this session",
            )
        )


def _skill_name(name: str) -> str:
    return f"skills::{name}"


def _document(skill_dir: Path, layout: ProjectLayout) -> str | None:
    """``SKILL.md`` rendered the way every surface's mirror writes it."""
    from ..placeholders import expand_fsm_edges_token, expand_path_placeholders

    source = skill_dir / "SKILL.md"
    if not source.is_file():
        return None
    return expand_fsm_edges_token(expand_path_placeholders(source.read_text(), layout), layout)


def _rule_name(name: str) -> str:
    return f"rules::{name}"


def _executable(path: Path) -> Executable:
    return Executable(
        path=path.resolve(), content_digest=hashlib.sha256(path.read_bytes()).hexdigest()
    )


def _override_name(label: str, identity: str) -> str:
    return identity if label == _RUNTIME_LAYER else f"{_OVERRIDE_NS}::{label}"


def _prompt_blocks(
    result: CompositionResult,
    overlays: Sequence[tuple[OverlayConfig, str]],
    layout: ProjectLayout,
) -> tuple[PromptBlock, ...]:
    """The blocks ``compose_sections`` assembles, member by member, each text
    expanded for the layout — the plan's rendering of them is byte-equal to the
    expanded output, which the parity tests hold."""
    from ..placeholders import expand_path_placeholders
    from ..resolver import read_rule_body
    from ..role_catalog import expand_role_catalog

    def member(name: str, text: str, heading: str | None) -> PromptMember:
        expanded = expand_role_catalog(expand_path_placeholders(text, layout), layout.root)
        return PromptMember(name, expanded, heading)

    blocks: list[PromptBlock] = []
    if result.priorities:
        numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
        blocks.append(
            PromptBlock("PRIORITIES", (member(f"{result.name}::priorities", numbered, None),))
        )

    named: dict[str, str] = {}
    for trait, text in result.trait_injections.items():
        named.setdefault(text, f"{trait}::prompt")
    if result.role_injection:
        named.setdefault(result.role_injection, f"{result.name}::prompt")
    for layer, label in overlays:
        text = layer.injection_append.strip()
        if text:
            named.setdefault(text, f"{_OVERRIDE_NS}::{label}::prompt")
    body: list[PromptMember] = []
    for text in result.injections:
        if not text.strip():
            continue
        if text not in named:
            head = text.splitlines()[0][:80]
            raise AdaptError(
                f"an injection reached the prompt that no role, trait or overlay declared "
                f"(starts {head!r}); pass every layer the composer applied"
            )
        body.append(member(named[text], text, None))
    if body:
        blocks.append(PromptBlock(None, tuple(body)))

    rules = tuple(
        member(_rule_name(rule.name), body_text, rule.name)
        for rule in result.rules
        if rule.source_path and (body_text := read_rule_body(rule.source_path))
    )
    if rules:
        blocks.append(PromptBlock("RULES", rules))

    user_rules: list[PromptMember] = []
    for path in result.user_rules:
        try:
            body_text = path.read_text()
        except OSError:
            continue  # compose_sections skips it too, with its own warning
        if body_text.strip():
            user_rules.append(member(_rule_name(path.stem), body_text, path.stem))
    if user_rules:
        blocks.append(PromptBlock("USER RULES", tuple(user_rules)))
    return tuple(blocks)


def _hooks(result: CompositionResult, diagnostics: list[Diagnostic]) -> Hooks:
    """The runtime channel a surface wires, and everything attached elsewhere —
    git events, worktree points, check bindings, consent points — one row per
    point (ADR-0035 D6).

    A declared script that is missing or not executable is the author's to fix:
    it becomes a diagnostic and no hook, on every channel alike.
    """
    from ai_hats_wt import WT_TEARDOWN_EVENTS, parse_worktree_carry

    from ..libraries.models import SkillMetadata

    runtime: list[RuntimeHook] = []
    external: list[ExternalHook] = []
    sources = {s.name: s.source_path for s in result.skills}

    def wired(skill_name: str, script: str, channel: str, at: str) -> Executable | None:
        declared = sources[skill_name] / script
        if not declared.is_file():
            what = f"missing at {declared}"
        elif not os.access(declared, os.X_OK):
            what = f"not executable: {declared}"
        else:
            return _executable(declared)
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"{channel} hook {at} of {_skill_name(skill_name)}: {script} is {what}; "
                "this hook will not run in this session",
            )
        )
        return None

    def attached(app: str, at: str, run: Executable, by: str) -> ExternalHook:
        return ExternalHook(app=app, object=None, at=at, run=run, on_error=None, declared_by=by)

    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        by = _skill_name(skill.name)
        for event, scripts in metadata.git_hooks.items():
            for script in scripts:
                if run := wired(skill.name, script, "git", event):
                    external.append(attached("git", event, run, by))
        for event, hooks in metadata.runtime_hooks.items():
            for hook in hooks:
                if run := wired(skill.name, hook.script, "runtime", f"{event}/{hook.matcher}"):
                    runtime.append(RuntimeHook(at=HookEvent(event), matcher=hook.matcher, run=run))
        carry = parse_worktree_carry(metadata.worktree, skill.name)
        for hook in carry.wt_in:
            if run := wired(skill.name, hook.script, "worktree", "create"):
                external.append(attached("wt", "create", run, by))
        for hook in carry.wt_out:
            if run := wired(skill.name, hook.script, "worktree", "teardown"):
                for event in hook.on or WT_TEARDOWN_EVENTS:
                    external.append(attached("wt", f"teardown[{event}]", run, by))

    for check in result.checks:
        run = _executable(check.script_path)
        for at in check.at:
            external.append(
                ExternalHook(
                    app=check.app,
                    object=".".join(check.path) or None,
                    at=at,
                    run=run,
                    on_error=OnError(check.on_error),
                    declared_by=check.declared_by,
                )
            )
    for point in result.consent:
        external.append(
            ExternalHook(
                app=point.app,
                object=".".join(point.path) or None,
                at=point.selector,
                run=None,
                on_error=None,
                declared_by=point.declared_by,
            )
        )
    return Hooks(runtime=tuple(runtime), external=tuple(external))


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
