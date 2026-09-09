"""Skill-declared hook collection — pure derivations over a CompositionResult.

Moved out of ``composer`` (HATS-865): consumed on BOTH sides of the composition
boundary (providers wiring AND runtime bricks), so the home must be a neutral
leaf that never imports the composition layer (``test_import_hygiene`` gates).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult
from ai_hats_wt import WorktreeHook, parse_worktree_carry

from .models import RuntimeHook, SkillMetadata

if TYPE_CHECKING:
    from .materialization import Materializer


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
            collected.setdefault(event, []).extend((skill.name, hook) for hook in hooks)
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
                collected.setdefault(kind, []).extend((skill.name, hook) for hook in hooks)
    return collected


def resolve_skill_script(
    result: CompositionResult, skill_name: str, script_path: str
) -> Path | None:
    """Resolve a script declared in a skill's metadata to an absolute path.

    Returns ``None`` when the declaring skill is absent from ``result`` or the
    file does not exist. The git and worktree channels skip such a hook; the
    runtime channel goes through :func:`composed_rows`, which reports instead.
    """
    for skill in result.skills:
        if skill.name != skill_name:
            continue
        candidate = (skill.source_path / script_path).resolve()
        if candidate.exists():
            return candidate
    return None


class RuntimeHookMirrorError(RuntimeError):
    """A declared hook script is in its skill but not executable in the session mirror.

    The mirror is ai-hats's own write, so this is never the skill author's to
    fix — and a gate that cannot run must not be wired as if it could.
    """


def composed_rows(
    result: CompositionResult | None,
    skills_dir: Path,
    *,
    port: Materializer,
) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """``({event: [row, ...]}, notices)`` — the rows every surface's manifest holds.

    Commands are absolute and point into the session's own skill mirror, so a
    hook runs beside the files its skill ships from any cwd. A declared script
    that is not there is never dropped in silence: absent from the skill itself
    is the author's to fix, so it becomes a notice and no row; present in the
    skill but not in the mirror is ours, so it raises.
    """
    rows: dict[str, list[dict[str, str]]] = {}
    notices: list[str] = []
    if result is None:
        return rows, notices
    sources = {skill.name: skill.source_path for skill in result.skills}
    for event, entries in collect_runtime_hooks(result).items():
        for skill_name, hook in entries:
            declared = sources[skill_name] / hook.script
            if not declared.is_file():
                notices.append(_not_running(skill_name, event, hook, f"missing at {declared}"))
                continue
            if not os.access(declared, os.X_OK):
                notices.append(_not_running(skill_name, event, hook, f"not executable: {declared}"))
                continue
            command = skills_dir / skill_name / hook.script
            if not port.executable_at(command):
                raise RuntimeHookMirrorError(
                    f"{_hook_label(skill_name, event, hook)}: {hook.script} is in the skill "
                    f"({sources[skill_name]}) but not an executable file in the session "
                    f"mirror at {command}; ai-hats did not materialize it, and a gate "
                    "that cannot run is not wired"
                )
            rows.setdefault(event, []).append(
                {
                    "matcher": hook.matcher,
                    "command": str(command),
                    "tag": f"ai-hats:{skill_name}:{event}:{hook.matcher}:{Path(hook.script).stem}",
                }
            )
    return rows, notices


def _hook_label(skill_name: str, event: str, hook: RuntimeHook) -> str:
    return f"runtime hook {event}/{hook.matcher} of skill {skill_name!r}"


def _not_running(skill_name: str, event: str, hook: RuntimeHook, what: str) -> str:
    return (
        f"{_hook_label(skill_name, event, hook)}: {hook.script} is {what}; this gate "
        "will not run in this session. Fix the script or drop its declaration in SKILL.md"
    )
