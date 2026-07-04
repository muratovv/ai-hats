"""Skill-declared hook collection — pure derivations over a CompositionResult.

Moved out of ``composer`` (HATS-865): consumed on BOTH sides of the composition
boundary (providers wiring AND runtime bricks), so the home must be a neutral
leaf that never imports the composition layer (``test_import_hygiene`` gates).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core import CompositionResult
from ai_hats_wt import WorktreeHook, parse_worktree_carry

from .models import RuntimeHook, SkillMetadata


def collect_runtime_hooks(
    result: CompositionResult,
) -> dict[str, list[tuple[str, RuntimeHook]]]:
    """Walk composed skills and group their declared runtime hooks by event.

    Returns ``{event_name: [(skill_name, RuntimeHook), ...]}``. Validation
    (unknown event, malformed row) already happened at
    :meth:`SkillMetadata.from_skill_dir` time and fails loud there.
    """
    collected: dict[str, list[tuple[str, RuntimeHook]]] = {}
    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        if not metadata.runtime_hooks:
            continue
        for event, hooks in metadata.runtime_hooks.items():
            collected.setdefault(event, []).extend(
                (skill.name, hook) for hook in hooks
            )
    return collected


def collect_worktree_hooks(
    result: CompositionResult,
) -> dict[str, list[tuple[str, WorktreeHook]]]:
    """Walk composed skills and group their worktree lifecycle hooks by kind.

    Returns ``{"wt_in": [(skill_name, WorktreeHook), ...], "wt_out": [...]}`` —
    only non-empty kinds appear (HATS-823). This is the compose-time typed
    chokepoint (HATS-863): ``SkillMetadata`` carries the ``worktree:`` block
    opaque; :func:`ai_hats_wt.parse_worktree_carry` validates HERE and fails
    loud on a malformed row. Mirrors :func:`collect_runtime_hooks`.
    """
    collected: dict[str, list[tuple[str, WorktreeHook]]] = {}
    for skill in result.skills:
        carry = parse_worktree_carry(
            SkillMetadata.from_skill_dir(skill.source_path).worktree, skill.name
        )
        if carry.is_empty():
            continue
        for kind, hooks in (("wt_in", carry.wt_in), ("wt_out", carry.wt_out)):
            if hooks:
                collected.setdefault(kind, []).extend(
                    (skill.name, hook) for hook in hooks
                )
    return collected


def resolve_skill_script(
    result: CompositionResult, skill_name: str, script_path: str
) -> Path | None:
    """Resolve a script declared in a skill's metadata to an absolute path.

    Returns ``None`` when the declaring skill is absent from ``result`` or the
    file does not exist — callers (materialize, provider wiring) MUST skip such
    a hook so a settings.json entry never points at a non-existent script.
    """
    for skill in result.skills:
        if skill.name != skill_name:
            continue
        candidate = (skill.source_path / script_path).resolve()
        if candidate.exists():
            return candidate
    return None
