"""The roots of the candidate library and the one impure input of processing:
``read_roots`` walks each root once and hands every later stage a value.

A root is a logical tree (a symlink inside it reads as its contents), walked in
sorted order; a folder holding ``SKILL.md`` is one entity and is not entered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..diagnostics import Diagnostic, Level
from ..paths.constants import LIBRARY_LAYERS
from ..provenance import ComponentLayer
from .errors import ProcessingError
from .parse import Entity, Hook, parse_md, parse_skill, parse_yaml, place_payload

EXPERIMENTAL_DIRNAME = "experimental"
_SKIPPED = frozenset({"__pycache__"})


@dataclass(frozen=True)
class Root:
    path: Path
    #: Who may be named from files in this root (ADR-0035 D2). Tests set it;
    #: the candidate chain sets it for a real library.
    owner: ComponentLayer = ComponentLayer.BUILT_IN


@dataclass(frozen=True)
class Layer:
    root: Root
    entities: tuple[Entity, ...]


@dataclass(frozen=True)
class Library:
    #: Chain order, lowest priority first (ADR-0035 D3).
    layers: tuple[Layer, ...]


def root_owner(
    path: Path,
    *,
    project_dir: Path,
    user_roots: Sequence[Path],
    project_roots: Sequence[Path],
) -> ComponentLayer:
    """Owner of a non-builtin root. User roots win over project roots when a
    directory is listed in both — the file is the user's."""
    resolved = path.resolve()
    if _within(resolved, user_roots):
        return ComponentLayer.GLOBAL
    if _within(resolved, project_roots) or _within(resolved, (project_dir,)):
        return ComponentLayer.PROJECT
    return ComponentLayer.BUILT_IN


def _within(path: Path, roots: Sequence[Path]) -> bool:
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if path == resolved or resolved in path.parents:
            return True
    return False


def candidate_roots(
    library_paths: Sequence[Path],
    *,
    builtin_root: Path | None,
    owner_of: Callable[[Path], ComponentLayer] | None = None,
) -> list[Root]:
    """Today's chain mapped onto its ``experimental/`` copies (ADR-0034 D6): a
    shipped layer under ``builtin_root`` reads ``<builtin_root>/experimental/<layer>``,
    any other root reads ``<root>/experimental``; a copy that does not exist is
    not a root. A shipped layer is ``built-in``; ``owner_of`` classifies the rest."""
    roots: list[Root] = []
    for path in library_paths:
        builtin = (
            builtin_root is not None and path.parent == builtin_root and path.name in LIBRARY_LAYERS
        )
        if builtin:
            candidate = builtin_root / EXPERIMENTAL_DIRNAME / path.name
            owner = ComponentLayer.BUILT_IN
        else:
            candidate = path / EXPERIMENTAL_DIRNAME
            owner = owner_of(path) if owner_of is not None else ComponentLayer.BUILT_IN
        if candidate.is_dir():
            roots.append(Root(candidate, owner=owner))
    return roots


def read_roots(roots: Sequence[Root], diagnostics: list[Diagnostic]) -> Library:
    """Every root read whole and once; a root named twice is read once, a root
    inside another refuses with both paths (a file may belong to one root)."""
    unique: list[Root] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(root)
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            ra, rb = a.path.resolve(), b.path.resolve()
            if ra == rb or ra.is_relative_to(rb) or rb.is_relative_to(ra):
                raise ProcessingError(
                    f"nested library roots: {a.path} and {b.path} — a file would have two names"
                )
    return Library(layers=tuple(Layer(root, _walk(root, diagnostics)) for root in unique))


def _walk(root: Root, diagnostics: list[Diagnostic]) -> tuple[Entity, ...]:
    entities: list[Entity] = []
    visited: set[Path] = set()

    def visit(folder: Path, segments: tuple[str, ...]) -> None:
        real = folder.resolve()
        if real in visited:
            return
        visited.add(real)
        for child in sorted(folder.iterdir()):
            if child.name.startswith(".") or child.name in _SKIPPED:
                continue
            if child.is_dir():
                if (child / "SKILL.md").is_file():
                    name = "::".join((*segments, child.name))
                    skill = parse_skill(child, name)
                    if skill is None:
                        diagnostics.append(
                            Diagnostic(
                                Level.WARN,
                                "SKILL.md declares no ai_hats.type — the folder is payload, "
                                "not a skill",
                                where=child / "SKILL.md",
                            )
                        )
                    else:
                        entities.append(replace(skill, owner=root.owner))
                    continue
                visit(child, (*segments, child.name))
                continue
            if not child.is_file():
                continue
            name = "::".join((*segments, child.name.rsplit(".", 1)[0]))
            if child.suffix == ".yaml":
                entity = parse_yaml(child, name)
            elif child.suffix == ".md":
                entity = parse_md(child, name)
            else:
                continue
            if entity is None:
                diagnostics.append(
                    Diagnostic(
                        Level.WARN,
                        "no 'type' — read as payload; a hook or prompt with a mistyped "
                        "'type' would land here silently",
                        where=child,
                    )
                )
            else:
                if isinstance(entity, Hook):
                    entity = place_payload(entity, root.path)
                entities.append(replace(entity, owner=root.owner))

    visit(root.path, ())
    return tuple(entities)
