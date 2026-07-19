"""Consumer lifecycle-hook channel: union collection + materialization
(HATS-1023, epic HATS-1014 K4).

Library skills declare ``lifecycle_hooks:`` (bash gates on rack FSM edges) and
``plan_sections:`` (extra plan-gate checklist entries) in SKILL.md frontmatter
``ai_hats:``. Unlike every ``_collect_*`` in :mod:`ai_hats.hook_collection`
(per-role, over a CompositionResult), the collector here is a UNION over ALL
skills of ALL library layers — lifecycle gates are role-independent (FSM doc
§5). Materialization is managed-dir + manifest + sweep (the
``materialize_worktree_hooks`` pattern) into
``<ai_hats_dir>/tracker/lifecycle-hooks/<from>--<to>.d/`` plus a
``plan-sections.yaml`` catalog, with a LOUD health-check: a broken declared
script fails materialization naming the skill — never a silent skip that dies
on a transaction later (HYP-078 / HATS-961).
"""  # comment-length: allow — module contract (union vs per-role, loud health-check)

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import yaml

from ai_hats_core.safe_delete import discard as _safe_discard
from ai_hats_core.safe_delete import replace as _safe_replace

from .models import ComponentType, SkillMetadata, resolve_namespace
from .paths import tracker_dir
from .resolver import LibraryResolver

_MANAGED_HEADER = "# ai-hats managed — do not edit"
MANIFEST_NAME = ".manifest"
PLAN_SECTIONS_FILENAME = "plan-sections.yaml"


class LifecycleHookError(Exception):
    """A consumer lifecycle declaration is broken — loud by design (HYP-078):
    the fault surfaces at materialization, never as a silent no-op gate."""


def lifecycle_hooks_dir(project_dir: Path) -> Path:
    """Managed consumer-hook root: ``<ai_hats_dir>/tracker/lifecycle-hooks/``."""
    return tracker_dir(project_dir) / "lifecycle-hooks"


def managed_lifecycle_hook_filename(skill_name: str, script: str) -> str:
    """``<skill>-<basename>`` dest name (the git/wt-hooks convention); both
    parts reduced to their last path segment so a declaration cannot traverse
    out of the managed dir."""
    return f"{Path(resolve_namespace(skill_name)).name}-{Path(script).name}"


def _valid_event_names() -> set[str]:
    """Every ``<from>--<to>`` pair of rack topology states (+ the execute
    reclaim self-loop) — the full product, not just legal edges: forced
    transitions fire real non-topology edge keys (mirror of the K3 safety
    subscriptions in ``rack_wiring``)."""
    # Deferred so importing this module (and hooks_manager) never imports the
    # rack; the one-directional boundary stays rack -/-> integrator.
    from ai_hats_rack import load_backlog

    states = load_backlog().topology.states
    return {f"{a}--{b}" for a in states for b in states if a != b or a == "execute"}


def _iter_library_skills(library_paths: Sequence[Path]):
    """Yield ``(skill_name, skill_dir, SkillMetadata)`` for EVERY library skill
    (union scope), deterministically sorted by name; layered shadowing follows
    the resolver (last layer wins)."""
    resolver = LibraryResolver(list(library_paths))
    for name in resolver.list_components(ComponentType.SKILL):
        skill_dir = resolver.resolve_skill_dir(name)
        if skill_dir is None:
            continue
        yield name, skill_dir, SkillMetadata.from_skill_dir(skill_dir)


def collect_lifecycle_hooks(
    library_paths: Sequence[Path],
) -> dict[str, list[tuple[str, Path]]]:
    """Union of ``lifecycle_hooks:`` declarations over ALL library skills.

    Returns ``{event: [(skill_name, script_source_path), ...]}`` with events
    validated against the rack topology — an unknown event name is a loud
    error, not a skip (a typo'd edge would otherwise become fail-closed chaos
    at transition time, FSM doc §5.1).
    """
    valid_events = _valid_event_names()
    collected: dict[str, list[tuple[str, Path]]] = {}
    for name, skill_dir, meta in _iter_library_skills(library_paths):
        for event, scripts in meta.lifecycle_hooks.items():
            if event not in valid_events:
                raise LifecycleHookError(
                    f"lifecycle_hooks: skill '{name}' declares unknown event "
                    f"'{event}' — expected a '<from>--<to>' pair of rack FSM "
                    f"states (e.g. 'plan--execute')"
                )
            for script in scripts:
                collected.setdefault(event, []).append((name, skill_dir / script))
    return collected


def collect_plan_sections(library_paths: Sequence[Path]) -> list[dict[str, object]]:
    """Union of ``plan_sections:`` declarations over ALL library skills.

    Deduped by section name; ``required`` is the OR over declarations (a
    section any consumer marks required stays required). Deterministic:
    skills in sorted order, sections in declaration order.
    """
    ordered: list[str] = []
    required: dict[str, bool] = {}
    for _name, _skill_dir, meta in _iter_library_skills(library_paths):
        for entry in meta.plan_sections:
            section_name = str(entry["name"])
            if section_name not in required:
                ordered.append(section_name)
                required[section_name] = bool(entry.get("required", True))
            else:
                required[section_name] = required[section_name] or bool(
                    entry.get("required", True)
                )
    return [{"name": n, "required": required[n]} for n in ordered]


def _health_check(skill: str, event: str, src: Path) -> bytes:
    """Validate one declared script; return its bytes. LOUD on any defect —
    the whole point of HATS-961/HYP-078: a hook that cannot run must fail the
    materialization naming the skill, not die silently on a transition."""
    label = f"lifecycle_hooks: skill '{skill}', event '{event}', script '{src.name}'"
    if not src.is_file():
        raise LifecycleHookError(
            f"{label}: declared file not found at {src} — fix the declaration "
            f"or restore the script"
        )
    data = src.read_bytes()
    if not data.strip():
        raise LifecycleHookError(f"{label}: script is empty — a no-op gate is a broken gate")
    if not data.startswith(b"#!"):
        raise LifecycleHookError(
            f"{label}: script has no shebang ('#!') first line — it would fail "
            f"to exec at transition time"
        )
    return data


def expected_lifecycle_files(library_paths: Sequence[Path]) -> dict[str, bytes]:
    """Managed relpath -> expected bytes for the current library declarations.

    Single source for BOTH the materializer and the session-start drift
    detector (the ``expected_git_hook_files`` pattern): keys are
    ``<event>.d/<skill>-<basename>`` per script plus ``plan-sections.yaml``
    when any consumer declares sections. Health-checks every script (loud).
    """
    expected: dict[str, bytes] = {}
    declared = collect_lifecycle_hooks(library_paths)
    for event in sorted(declared):
        for skill, src in declared[event]:
            data = _health_check(skill, event, src)
            rel = f"{event}.d/{managed_lifecycle_hook_filename(skill, src.name)}"
            if rel in expected and expected[rel] != data:
                raise LifecycleHookError(
                    f"lifecycle_hooks: two declarations collide on managed name "
                    f"'{rel}' with different contents — rename one script"
                )
            expected[rel] = data
    sections = collect_plan_sections(library_paths)
    if sections:
        expected[PLAN_SECTIONS_FILENAME] = yaml.safe_dump(
            sections, sort_keys=False, allow_unicode=True
        ).encode("utf-8")
    return expected


def _read_manifest(path: Path) -> set[str]:
    from .sweeper import read_marker_names  # deferred: sweeper↔hooks cycle guard

    return read_marker_names(path)


def materialize_lifecycle_hooks(project_dir: Path, library_paths: Sequence[Path]) -> None:
    """Bring ``<ai_hats_dir>/tracker/lifecycle-hooks/`` in sync with the union
    of library declarations: write scripts 0755 + ``plan-sections.yaml``,
    sweep manifest-tracked strays, keep a clean project clean (no dir/manifest
    when nothing is or was declared — the ``materialize_worktree_hooks``
    mirror). Loud on any broken declaration (health-check)."""
    target_dir = lifecycle_hooks_dir(project_dir)
    manifest_path = target_dir / MANIFEST_NAME

    previous = _read_manifest(manifest_path)
    expected = expected_lifecycle_files(library_paths)
    new_names = set(expected)

    if not new_names and not previous:
        return  # nothing now or before → no dir/manifest

    target_dir.mkdir(parents=True, exist_ok=True)
    for rel in sorted(expected):
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        _safe_replace(
            dest,
            expected[rel],
            reason="materialize-lifecycle-hook",
            project_dir=project_dir,
            mode=0o755 if rel != PLAN_SECTIONS_FILENAME else None,
        )
    for stale in sorted(previous - new_names):
        _safe_discard(target_dir / stale, reason="materialize-lifecycle-sweep",
                      project_dir=project_dir)
    for child in target_dir.iterdir():
        if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
            child.rmdir()  # safe-delete: ok empty-dir
    _safe_replace(
        manifest_path,
        (_MANAGED_HEADER + "\n" + "\n".join(sorted(new_names)) + "\n").encode(),
        reason="materialize-lifecycle-manifest",
        project_dir=project_dir,
    )
