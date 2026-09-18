"""What a session's composition lists, by kind — the one reader of it.

Since 2026-09-16 a session records the composition half of its plan and
``composition_names`` is its reader; every consumer that names what loaded
(``audit.md``, the reviewer's prompt) projects through here. Older sessions
hold a snapshot nothing writes any more; it only comes back off disk, and
``snapshot_names`` reads it, kept apart from the record's reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# A member the trace does not attribute came with the launched expression.
EXPRESSION = "expression"
# A snapshot name its provenance map does not place ships with ai-hats.
BUILT_IN = "built-in"

_RULES = "rules::"
_SKILLS = "skills::"


@dataclass(frozen=True)
class CompositionNames:
    """Each kind as ``(name, brought_by)`` pairs, the ``rules::`` / ``skills::``
    prefix stripped from the name."""

    traits: tuple[tuple[str, str], ...]
    rules: tuple[tuple[str, str], ...]
    skills: tuple[tuple[str, str], ...]


def composition_names(record: Mapping) -> CompositionNames:
    """Traits are the trace's remaining terms; rules the prompt members named
    ``rules::``; skills the record's skills — each tagged by what brought it."""
    brought = {
        t["term"]: t["brought_by"] for t in record.get("trace", ()) if t.get("removed_by") is None
    }

    def attributed(full_name: str) -> tuple[str, str]:
        bare = full_name.split("::", 1)[1] if full_name.startswith((_RULES, _SKILLS)) else full_name
        return bare, brought.get(full_name, EXPRESSION)

    traits = tuple(attributed(term) for term in brought if not term.startswith((_RULES, _SKILLS)))
    rules = tuple(
        attributed(str(m["name"]))
        for block in record.get("prompt", {}).get("blocks", ())
        for m in block.get("members", ())
        if str(m.get("name", "")).startswith(_RULES)
    )
    skills = tuple(attributed(str(s["name"])) for s in record.get("skills", ()))
    return CompositionNames(traits=traits, rules=rules, skills=skills)


def snapshot_names(snapshot: Mapping) -> CompositionNames:
    """The pre-2026-09-16 snapshot: ``traits`` / ``rules`` / ``skills`` name
    lists, each name tagged by its layer in ``provenance`` (``built-in``,
    ``global``, ``project``)."""
    provenance = snapshot.get("provenance") or {}

    def kind(key: str) -> tuple[tuple[str, str], ...]:
        layers = provenance.get(key) or {}
        return tuple((name, layers.get(name, BUILT_IN)) for name in snapshot.get(key) or ())

    return CompositionNames(traits=kind("traits"), rules=kind("rules"), skills=kind("skills"))


def is_record(composition: Mapping) -> bool:
    """Whether a stored composition is the plan's record, not the older snapshot."""
    return "prompt" in composition


def stored_composition_names(composition: Mapping) -> CompositionNames:
    """A composition read back off disk, whichever shape its session wrote."""
    if is_record(composition):
        return composition_names(composition)
    return snapshot_names(composition)
