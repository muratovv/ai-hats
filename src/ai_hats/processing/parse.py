"""Parsing (ADR-0035 D1): one file, one entity, checked against the schema of
its ``type`` (ADR-0034 D3) — an unknown key, a missing required one or a value
of the wrong shape refuses; a file without a ``type`` is payload."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from ..frontmatter import FrontmatterError, parse_frontmatter
from .errors import ProcessingError
from .expr import Expr, parse_expr

SCHEMA_VERSION = 1
_FENCE = "---"

#: Per type: the keys an author may write beyond ``type`` / ``schema_version``,
#: and which of them are required. A hook's keys are the union here; the kind
#: narrows them in ``_check_hook``.
SCHEMAS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "composite": (frozenset({"expr"}), frozenset({"tags", "summary"})),
    "prompt": (frozenset({"block"}), frozenset({"heading"})),
    "hook": (
        frozenset({"kind"}),
        frozenset({"at", "run", "matcher", "app", "object", "on_error", "on", "operation"}),
    ),
    "operation": (
        frozenset({"surface", "reader", "binds_ticket"}),
        frozenset({"hook_only_because"}),
    ),
    "override": (frozenset({"apply", "expr"}), frozenset()),
    "skill": (frozenset(), frozenset({"requires", "triggers", "skip"})),
}
#: Types an expression may name (ADR-0034 D1, "Терм?").
TERM_TYPES = frozenset({"composite", "prompt", "hook", "skill"})
_ON_PATH = ("scripts", "bin")


@dataclass(frozen=True)
class Composite:
    name: str
    path: Path
    expr: Expr
    tags: tuple[str, ...]
    summary: str | None


@dataclass(frozen=True)
class PromptEntity:
    name: str
    path: Path
    block: str
    heading: str | None
    #: The bytes after the closing fence, exactly as written.
    text: str


@dataclass(frozen=True)
class SkillEntity:
    name: str
    #: The folder — the tree below it is payload.
    path: Path
    #: ``SKILL.md`` as authored; emit expands it for the layout.
    document: str
    content_digest: str
    on_path: tuple[str, ...]


@dataclass(frozen=True)
class Hook:
    """One hook file. ``run`` is the payload inside the layer root; consent has none."""

    name: str
    path: Path
    kind: str
    at: str
    #: The ``run`` text as written, relative to the file. ``None`` for consent.
    run_text: str | None
    #: Absolute payload, set once the root is known. ``None`` until then, and for consent.
    run: Path | None
    #: Payload path from the layer root — the join identity. ``None`` for consent.
    run_key: str | None
    matcher: str | None
    app: str | None
    object: str | None
    on_error: str | None
    on: tuple[str, ...]
    operation: str | None


@dataclass(frozen=True)
class Declared:
    """An ``operation`` or ``override``: schema-checked, not yet consumed."""

    name: str
    path: Path
    type: str
    fields: Mapping[str, object]


Entity = Composite | PromptEntity | SkillEntity | Hook | Declared


def entity_type(entity: Entity) -> str:
    if isinstance(entity, Composite):
        return "composite"
    if isinstance(entity, PromptEntity):
        return "prompt"
    if isinstance(entity, SkillEntity):
        return "skill"
    if isinstance(entity, Hook):
        return "hook"
    return entity.type


def split_frontmatter(raw: str, path: Path) -> tuple[Mapping[str, object] | None, str]:
    """``(mapping, text)`` — the text is every byte after the closing fence
    line; ``(None, raw)`` where no fence opens and closes the file."""
    try:
        mapping = parse_frontmatter(raw)
    except FrontmatterError as exc:
        raise ProcessingError(f"{path}: {exc}") from exc
    if not raw.startswith(_FENCE):
        return None, raw
    lines = raw.splitlines(keepends=True)
    if lines[0].strip() != _FENCE:
        return None, raw
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            return mapping, "".join(lines[i + 1 :])
    return None, raw


def _check_schema(path: Path, type_: str, data: Mapping[str, object], *, where: str = "") -> None:
    if type_ not in SCHEMAS:
        raise ProcessingError(f"{path}: unknown type {type_!r}")
    required, optional = SCHEMAS[type_]
    prefix = f"{path}: {where}" if where else f"{path}: "
    version = data.get("schema_version")
    if version is None:
        raise ProcessingError(f"{prefix}missing 'schema_version'")
    if version != SCHEMA_VERSION:
        raise ProcessingError(
            f"{prefix}schema_version {version!r} is not supported (only {SCHEMA_VERSION})"
        )
    for key in data:
        if key not in required and key not in optional and key not in ("type", "schema_version"):
            raise ProcessingError(f"{prefix}unknown key {key!r} for type {type_!r}")
    for key in required:
        if key not in data:
            raise ProcessingError(f"{prefix}missing {key!r}, required for type {type_!r}")


def _string(path: Path, data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ProcessingError(f"{path}: {key!r} must be a string, got {type(value).__name__}")
    return value


def _optional_string(path: Path, data: Mapping[str, object], key: str) -> str | None:
    return _string(path, data, key) if key in data else None


def _string_list(path: Path, data: Mapping[str, object], key: str) -> tuple[str, ...]:
    if key not in data:
        return ()
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProcessingError(f"{path}: {key!r} must be a list of strings")
    return tuple(value)


def _expr(path: Path, data: Mapping[str, object]) -> Expr:
    try:
        return parse_expr(_string(path, data, "expr"))
    except ProcessingError as exc:
        raise ProcessingError(f"{path}: expr: {exc}") from exc


#: Required keys, then optional ones, beyond ``kind``.
_HOOK_KINDS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "git": (frozenset({"at", "run"}), frozenset()),
    "runtime": (frozenset({"at", "matcher", "run"}), frozenset()),
    "workflow": (frozenset({"app", "at", "run", "on_error"}), frozenset({"object"})),
    "worktree": (frozenset({"at", "run"}), frozenset({"on"})),
    "consent": (frozenset({"operation", "at"}), frozenset()),
}
_ALWAYS = frozenset({"type", "schema_version", "kind"})


def _check_hook(path: Path, data: Mapping[str, object]) -> None:
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in _HOOK_KINDS:
        listed = ", ".join(sorted(_HOOK_KINDS))
        raise ProcessingError(f"{path}: hook kind {kind!r} is not one of {listed}")
    required, optional = _HOOK_KINDS[kind]
    for key in data:
        if key not in required and key not in optional and key not in _ALWAYS:
            raise ProcessingError(f"{path}: {key!r} is not a field of a {kind} hook")
    for key in required:
        if key not in data:
            raise ProcessingError(f"{path}: a {kind} hook requires {key!r}")
    if kind == "workflow" and data["on_error"] not in ("refuse", "warn"):
        raise ProcessingError(f"{path}: on_error {data['on_error']!r} is not 'refuse' or 'warn'")
    if kind == "worktree" and data["at"] not in ("wt_in", "wt_out"):
        raise ProcessingError(f"{path}: worktree at {data['at']!r} is not 'wt_in' or 'wt_out'")
    if kind == "worktree" and data["at"] == "wt_in" and "on" in data:
        raise ProcessingError(f"{path}: wt_in takes no 'on'")


def _hook(path: Path, name: str, data: Mapping[str, object]) -> Hook:
    _check_hook(path, data)
    return Hook(
        name=name,
        path=path,
        kind=_string(path, data, "kind"),
        at=_string(path, data, "at"),
        run_text=_optional_string(path, data, "run"),
        run=None,
        run_key=None,
        matcher=_optional_string(path, data, "matcher"),
        app=_optional_string(path, data, "app"),
        object=_optional_string(path, data, "object"),
        on_error=_optional_string(path, data, "on_error"),
        on=_string_list(path, data, "on"),
        operation=_optional_string(path, data, "operation"),
    )


def place_payload(hook: Hook, root: Path) -> Hook:
    """Resolve ``run`` inside the layer root; a path that leaves it or is missing refuses."""
    if hook.kind == "consent":
        return hook
    if hook.run_text is None:
        raise ProcessingError(f"{hook.path}: missing 'run'")
    root_real = root.resolve()
    target = (hook.path.parent / hook.run_text).resolve()
    if target != root_real and root_real not in target.parents:
        raise ProcessingError(
            f"{hook.path}: run {hook.run_text!r} is outside the library root {root_real}"
        )
    if not target.is_file():
        raise ProcessingError(f"{hook.path}: run {hook.run_text!r} does not exist")
    return replace(hook, run=target, run_key=target.relative_to(root_real).as_posix())


def parse_yaml(path: Path, name: str) -> Entity | None:
    """A ``.yaml`` file: an entity by its ``type``, or ``None`` for payload."""
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ProcessingError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ProcessingError(f"{path}: not a mapping")
    if "type" not in data:
        return None
    type_ = data["type"]
    if not isinstance(type_, str):
        raise ProcessingError(f"{path}: 'type' must be a string")
    _check_schema(path, type_, data)
    if type_ == "composite":
        return Composite(
            name=name,
            path=path,
            expr=_expr(path, data),
            tags=_string_list(path, data, "tags"),
            summary=_optional_string(path, data, "summary"),
        )
    if type_ in ("prompt", "skill"):
        raise ProcessingError(f"{path}: type {type_!r} is written as a .md file, not .yaml")
    if type_ == "hook":
        return _hook(path, name, data)
    return Declared(name=name, path=path, type=type_, fields=dict(data))


def parse_md(path: Path, name: str) -> PromptEntity | None:
    """A ``.md`` file: a prompt by its frontmatter ``type``, or ``None`` for payload."""
    mapping, text = split_frontmatter(path.read_text(), path)
    if mapping is None or "type" not in mapping:
        return None
    type_ = mapping["type"]
    if not isinstance(type_, str):
        raise ProcessingError(f"{path}: 'type' must be a string")
    _check_schema(path, type_, mapping)
    if type_ != "prompt":
        raise ProcessingError(f"{path}: type {type_!r} is written as a .yaml file, not .md")
    return PromptEntity(
        name=name,
        path=path,
        block=_string(path, mapping, "block"),
        heading=_optional_string(path, mapping, "heading"),
        text=text,
    )


def parse_skill(folder: Path, name: str) -> SkillEntity | None:
    """A folder holding ``SKILL.md``: a skill by ``ai_hats.type``, or ``None``
    (payload) when the frontmatter declares no type."""
    from ..fs_digest import dir_digest

    document_path = folder / "SKILL.md"
    raw = document_path.read_text()
    mapping, _text = split_frontmatter(raw, document_path)
    ours = (mapping or {}).get("ai_hats")
    if not isinstance(ours, Mapping) or "type" not in ours:
        return None
    type_ = ours["type"]
    if type_ != "skill":
        raise ProcessingError(f"{document_path}: ai_hats.type {type_!r} is not 'skill'")
    _check_schema(document_path, "skill", ours, where="ai_hats: ")
    return SkillEntity(
        name=name,
        path=folder,
        document=raw,
        content_digest=dir_digest(folder),
        on_path=tuple(d for d in _ON_PATH if (folder / d).is_dir()),
    )
