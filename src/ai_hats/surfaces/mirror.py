"""The session's skill mirror as every surface derives it from the composition
half: the tree entries, the directories the agent calls scripts from, and the
rows a hook manifest names. One derivation, so no surface mirrors a skill, puts
a directory on PATH or wires a hook the others would not (ADR-0036 D2)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.materialization import MaterializationEntry, WriteKind, describe_write_text

from .plan import CompositionPlan, home_of, mirror_name

Rows = dict[str, list[dict[str, str]]]


def mirror_entries(composition: CompositionPlan, skills_root: Path) -> list[MaterializationEntry]:
    """Each skill's tree synced under ``skills_root``, its document written
    over the copy. No wipe — a session root is its own, and the sync leaves
    nothing stale behind."""
    entries: list[MaterializationEntry] = []
    for skill in composition.skills:
        dest = skills_root / mirror_name(skill)
        entries.append(
            MaterializationEntry(
                kind=WriteKind.COPY_TREE,
                target=dest,
                source=skill.path,
                tree_digest=skill.content_digest,
            )
        )
        if skill.document is not None:
            entries.append(describe_write_text(dest / "SKILL.md", skill.document))
    return entries


def path_dirs(composition: CompositionPlan, skills_root: Path) -> list[Path]:
    """The mirrored directories the agent calls scripts from, in composition order."""
    return [skills_root / mirror_name(s) / d for s in composition.skills for d in s.on_path]


def manifest_rows(composition: CompositionPlan, skills_root: Path) -> Rows:
    """``{event: [row, ...]}`` — a command points into the session's own
    mirror, derived from where the script lives in the library; the mirror is
    executable by construction (the tree sync keeps modes), so nothing is
    checked on disk."""
    rows: Rows = {}
    for hook in composition.hooks.runtime:
        home = home_of(hook.run, composition.skills)
        if home is None:
            raise ValueError(
                f"runtime hook {hook.at.value}/{hook.matcher} runs {hook.run.path}, "
                "outside every composed skill"
            )
        skill, inside = home
        name = mirror_name(skill)
        rows.setdefault(hook.at.value, []).append(
            {
                "matcher": hook.matcher,
                "command": str(skills_root / name / inside),
                "tag": f"ai-hats:{name}:{hook.at.value}:{hook.matcher}:{inside.stem}",
            }
        )
    return rows


__all__ = ["Rows", "manifest_rows", "mirror_entries", "path_dirs"]
