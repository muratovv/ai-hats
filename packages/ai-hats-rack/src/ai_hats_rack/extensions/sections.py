"""Config-driven plan-section catalog — the single source both the scaffold
and the gate read, so contract and enforcement can never drift (HATS-635).

The catalog is data: the built-in default is file-backed by the packaged
``plan-sections.yaml`` (HATS-1042); a consumer may load its own from YAML
(``load_sections``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .. import fastyaml
from ..errors import RackConfigError


@dataclass(frozen=True)
class Section:
    """One plan-template section. ``required=False`` (e.g. the HATS-621
    value-counter stage) is scaffolded but never blocks the gate."""

    name: str
    required: bool = True


class SectionCatalogError(RackConfigError):
    """A section catalog file is malformed."""


def _parse_sections(raw: object, source: str) -> tuple[Section, ...]:
    """A section catalog is a non-empty list of ``str`` or ``{name, required?}``."""
    if not isinstance(raw, list) or not raw:
        raise SectionCatalogError(f"{source}: expected a non-empty list of sections")
    out: list[Section] = []
    for item in raw:
        if isinstance(item, str):
            out.append(Section(name=item))
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(Section(name=item["name"], required=bool(item.get("required", True))))
        else:
            raise SectionCatalogError(f"{source}: bad section entry {item!r}")
    return tuple(out)


def load_sections(path: Path) -> tuple[Section, ...]:
    """Load a section catalog from YAML: a list of ``{name, required?}``."""
    return _parse_sections(fastyaml.load(path.read_text(encoding="utf-8")), str(path))


def _load_default_sections() -> tuple[Section, ...]:
    text = resources.files("ai_hats_rack").joinpath("plan-sections.yaml").read_text(encoding="utf-8")
    return _parse_sections(fastyaml.load(text), "ai_hats_rack/plan-sections.yaml")


DEFAULT_PLAN_SECTIONS: tuple[Section, ...] = _load_default_sections()


def merge_sections(
    base: tuple[Section, ...], extras: tuple[Section, ...]
) -> tuple[Section, ...]:
    """Extend a section catalog with consumer-declared sections (HATS-1023).

    Deduped by name, base-wins: a consumer cannot weaken (or retype) a stock
    section by re-declaring its name — the channel is append-only. Among the
    extras themselves the first occurrence wins, so a deterministic input
    order (sorted collection) yields a deterministic catalog.
    """
    seen = {s.name for s in base}
    merged = list(base)
    for section in extras:
        if section.name in seen:
            continue
        seen.add(section.name)
        merged.append(section)
    return tuple(merged)


def render_scaffold(sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS) -> str:
    """Render the plan.md scaffold template from the catalog.

    Section bodies are EMPTY on purpose: the per-section gate treats any
    pre-filled placeholder as "filled", which would defeat it (HATS-635).
    The result keeps ``{task_id}`` / ``{title}`` placeholders for ``.format()``.
    """
    parts = ["# Plan for {task_id}: {title}\n"]
    parts.extend(f"## {section.name}\n" for section in sections)
    return "\n".join(parts) + "\n"


def unfilled_sections(
    plan_text: str | None, sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS
) -> list[str]:
    """Names of REQUIRED sections with no body content in ``plan_text``.

    A section is filled when at least one non-whitespace line sits between
    its ``## <name>`` heading and the next level-2 heading (or EOF); an
    absent required heading counts as unfilled; ``None`` (no readable plan)
    flags every required section. Reads the SAME catalog the scaffold
    renders from (HATS-635 never-drift).
    """
    if plan_text is None:
        return [s.name for s in sections if s.required]

    # `^##\s+` is a level-2 heading only: `###` and the H1 title both fail.
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in plan_text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            bodies.setdefault(current, [])
        elif current is not None:
            bodies[current].append(line)

    unfilled: list[str] = []
    for section in sections:
        if not section.required:
            continue
        body = bodies.get(section.name)
        if body is None or not "".join(body).strip():
            unfilled.append(section.name)
    return unfilled
