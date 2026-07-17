"""Linked tasks + discovery context assembly (HATS-1024, K5; HATS-1028 registry).

Link mutations ride the same transaction window the kernel uses for a card
(task lock → load → mutate → work_log → SINGLE atomic persist; lock model §2.2).
The read side assembles the discovery package (flows §2.3): trimmed card + names
+ absolute paths + mtime, never content — unless explicitly selected via
``--with`` under a hard per-document byte ceiling (the F4 209 851-char lesson).
Every edge kind flows through the injected registry: ``link``/``unlink`` take
any configured kind, and reads project onto one ``links`` map (HATS-1028).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from filelock import FileLock, Timeout

from .docstore import DocInfo, DocStore, UnknownDocumentError
from .kernel import LOCK_TIMEOUT, Kernel, LockTimeoutError, UnknownTaskError
from .models import TaskCard, utc_now
from .registry import (
    DerivedLinkKindError,
    LinkKind,
    LinksRegistry,
    load_registry,
    resolve_links,
)

#: outcome documents surfaced (as paths) for each depends_on/related target.
LINKED_DOC_NAMES = ("summary.md", "retro.md")
#: the parent epic's design home — the one doc the old CLI force-injected.
PARENT_DOC_NAMES = ("plan.md",)
#: per-document ceiling for ``--with`` embeds (≈4K tokens); truncation is marked.
DEFAULT_MAX_BYTES = 16384


class SelfLinkError(Exception):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' cannot link to itself")


class UnknownSelectorError(Exception):
    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"Unknown --with selector {selector!r}: pass plan, summary, or doc:<name>")


# ----- link mutations (task-locked, single persist) ---------------------------


@dataclass(frozen=True)
class LinkResult:
    """Outcome of a link/unlink call; ``kinds`` names the link kinds touched."""

    task_id: str
    target: str
    kinds: tuple[str, ...]
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "target": self.target,
            "kinds": list(self.kinds),
            "changed": self.changed,
        }


def _kind_ids(kind: LinkKind, card: TaskCard) -> list[str]:
    """Live list handle for a many-arity kind (legacy field or links dict)."""
    if kind.legacy_field:
        return getattr(card, kind.legacy_field)
    return card.links.setdefault(kind.name, [])


def _add_link(kind: LinkKind, card: TaskCard, target: str) -> bool:
    """Add ``target`` under ``kind``; return whether anything changed."""
    if kind.legacy_field and kind.arity == "one":
        if getattr(card, kind.legacy_field) == target:
            return False
        setattr(card, kind.legacy_field, target)
        return True
    ids = _kind_ids(kind, card)
    if target in ids:
        return False
    ids.append(target)
    return True


def _remove_link(kind: LinkKind, card: TaskCard, target: str) -> bool:
    """Remove ``target`` from ``kind``; return whether anything changed."""
    if kind.legacy_field and kind.arity == "one":
        if getattr(card, kind.legacy_field) != target:
            return False
        setattr(card, kind.legacy_field, "")
        return True
    ids = _kind_ids(kind, card)
    if target not in ids:
        return False
    ids.remove(target)
    return True


def _stored_kind(registry: LinksRegistry, name: str) -> LinkKind:
    """Resolve a linkable kind — a derived kind is a typed, actionable refusal."""
    kind = registry.require(name)  # UnknownLinkKindError names the configured set
    if kind.derived:
        raise DerivedLinkKindError(kind.name, kind.inverse)
    return kind


def link_on_card(
    registry: LinksRegistry, card: TaskCard, target: str, kind: str = "related", *, actor: str = ""
) -> LinkResult:
    """Add ``target`` under ``kind`` to an already-loaded card (lock-free core).

    The caller owns the task lock, the target-existence check, and the single
    persist (composite transition or :func:`link`). Idempotent: an existing link
    returns ``changed=False`` and mutates nothing.
    """
    link_kind = _stored_kind(registry, kind)
    if target == card.id:
        raise SelfLinkError(card.id)
    if not _add_link(link_kind, card, target):
        return LinkResult(card.id, target, (), changed=False)
    card.log_work(f"Linked {target} ({link_kind.name})", actor=actor)
    return LinkResult(card.id, target, (link_kind.name,), changed=True)


def unlink_on_card(
    registry: LinksRegistry,
    card: TaskCard,
    target: str,
    kind: str | None = None,
    *,
    actor: str = "",
) -> LinkResult:
    """Remove ``target`` from an already-loaded card (lock-free core). With
    ``kind`` → that kind only; else every stored non-hierarchy kind."""
    if kind is not None:
        kinds: tuple[LinkKind, ...] = (_stored_kind(registry, kind),)
    else:
        hierarchy = registry.hierarchy_kind
        kinds = tuple(k for k in registry.stored_kinds() if k is not hierarchy)
    removed = tuple(k.name for k in kinds if _remove_link(k, card, target))
    if not removed:
        return LinkResult(card.id, target, (), changed=False)
    card.log_work(f"Unlinked {target} ({', '.join(removed)})", actor=actor)
    return LinkResult(card.id, target, removed, changed=True)


def link(
    tasks_dir: Path,
    task_id: str,
    target: str,
    kind: str = "related",
    *,
    registry: LinksRegistry | None = None,
    actor: str = "",
    lock_timeout: float = LOCK_TIMEOUT,
) -> LinkResult:
    """Add ``target`` to ``task_id`` under any configured, non-derived ``kind``.
    Thin lock wrapper over :func:`link_on_card`; idempotent."""
    reg = registry if registry is not None else load_registry()
    _stored_kind(reg, kind)  # kind refusal before the lock (order parity)
    if target == task_id:
        raise SelfLinkError(task_id)
    if not (tasks_dir / target / "task.yaml").exists():
        raise UnknownTaskError(target)

    def op(card: TaskCard) -> tuple[LinkResult, bool]:
        result = link_on_card(reg, card, target, kind, actor=actor)
        return result, result.changed

    return _locked_card_op(tasks_dir, task_id, op, lock_timeout)


def unlink(
    tasks_dir: Path,
    task_id: str,
    target: str,
    kind: str | None = None,
    *,
    registry: LinksRegistry | None = None,
    actor: str = "",
    lock_timeout: float = LOCK_TIMEOUT,
) -> LinkResult:
    """Remove ``target`` from ``task_id``. Thin lock wrapper over
    :func:`unlink_on_card`; idempotent, a dangling target is removable."""
    reg = registry if registry is not None else load_registry()

    def op(card: TaskCard) -> tuple[LinkResult, bool]:
        result = unlink_on_card(reg, card, target, kind, actor=actor)
        return result, result.changed

    return _locked_card_op(tasks_dir, task_id, op, lock_timeout)


def _locked_card_op(
    tasks_dir: Path,
    task_id: str,
    op: Callable[[TaskCard], tuple[LinkResult, bool]],
    lock_timeout: float,
) -> LinkResult:
    """Load → mutate → single atomic persist inside the task lock — the same
    transaction window the kernel uses (lock model §2.2)."""
    card_path = tasks_dir / task_id / "task.yaml"
    if not card_path.exists():
        raise UnknownTaskError(task_id)
    lock_path = tasks_dir / task_id / ".lock"
    lock = FileLock(str(lock_path), timeout=lock_timeout)
    try:
        with lock:
            card = TaskCard.from_yaml(card_path)
            result, dirty = op(card)
            if dirty:
                card.updated = utc_now()
                card.save(card_path)
    except Timeout as exc:
        raise LockTimeoutError(lock_path, f"link op on {task_id}", lock_timeout) from exc
    return result


# ----- read side: tree / ls rows / context package ----------------------------


def _id_key(task_id: str) -> tuple[str, int]:
    """Numeric-aware id ordering: HATS-999 sorts before HATS-1020."""
    m = re.search(r"(\d+)$", task_id)
    return (task_id[: m.start()] if m else task_id, int(m.group(1)) if m else -1)


def _mtime_iso(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_card(tasks_dir: Path, task_id: str) -> TaskCard | None:
    """Graceful loader for linked targets: dangling/corrupt → None, never raise."""
    path = tasks_dir / task_id / "task.yaml"
    if not path.exists():
        return None
    try:
        return TaskCard.from_yaml(path)
    except Exception:  # noqa: BLE001 — a broken neighbour must not sink the package
        return None


@dataclass(frozen=True)
class TreeNode:
    id: str
    state: str
    priority: str
    title: str
    children: tuple[TreeNode, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "priority": self.priority,
            "title": self.title,
            "children": [c.to_dict() for c in self.children],
        }


def build_tree(tasks_dir: Path, task_id: str) -> TreeNode:
    """Recursive children tree; a parent_task cycle is shown once, never looped."""
    kernel = Kernel(tasks_dir)
    root = kernel.get(task_id)
    if root is None:
        raise UnknownTaskError(task_id)
    seen = {task_id}

    def node(card: TaskCard) -> TreeNode:
        kids: list[TreeNode] = []
        for cid in sorted(kernel.children_of(card.id), key=_id_key):
            if cid in seen:
                continue
            seen.add(cid)
            child = _load_card(tasks_dir, cid)
            if child is not None:
                kids.append(node(child))
        return TreeNode(card.id, card.state, card.priority, card.title, tuple(kids))

    return node(root)


@dataclass(frozen=True)
class CardRow:
    """One compact listing row (ls results, epic children)."""

    id: str
    state: str
    priority: str
    title: str
    parent_task: str
    tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "priority": self.priority,
            "title": self.title,
            "parent_task": self.parent_task,
            "tags": list(self.tags),
        }


def scan_cards(
    tasks_dir: Path,
    *,
    grep: str | None = None,
    tag: str | None = None,
    state: str | None = None,
    parent: str | None = None,
) -> list[CardRow]:
    """Linear backlog scan, filters AND-combined; no index by design."""
    rows: list[CardRow] = []
    if not tasks_dir.is_dir():
        return rows
    needle = grep.lower() if grep else None
    for card_path in sorted(tasks_dir.glob("*/task.yaml"), key=lambda p: _id_key(p.parent.name)):
        try:
            card = TaskCard.from_yaml(card_path)
        except Exception:  # noqa: BLE001, S112 — one corrupt card must not kill the listing
            continue
        if state and card.state != state:
            continue
        if parent and card.parent_task != parent:
            continue
        if tag and tag not in card.tags:
            continue
        if needle and needle not in f"{card.title}\n{card.description}".lower():
            continue
        rows.append(
            CardRow(
                card.id, card.state, card.priority, card.title, card.parent_task, tuple(card.tags)
            )
        )
    return rows


@dataclass(frozen=True)
class DocRef:
    """Discovery pointer to one linked-task document: absolute path + freshness."""

    name: str
    path: Path
    mtime: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path), "mtime": self.mtime}


@dataclass(frozen=True)
class LinkView:
    """One target under one link kind: trimmed card head + outcome-doc paths."""

    kind: str
    id: str
    title: str
    state: str
    priority: str
    resolution: str
    docs: tuple[DocRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "priority": self.priority,
            "resolution": self.resolution or None,
            "docs": [d.to_dict() for d in self.docs],
        }


@dataclass(frozen=True)
class Inclusion:
    """One ``--with`` embed: full on-disk size vs the capped content carried."""

    task_id: str
    name: str
    path: Path
    size: int
    truncated: bool
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "truncated": self.truncated,
            "content": self.content,
        }


@dataclass(frozen=True)
class ContextPackage:
    task: TaskCard
    documents: tuple[DocInfo, ...]
    #: single, kind-keyed edge map (HATS-1028) — replaces the old scattered
    #: parent/depends_on/related/children fields. Registry order; deduped ids.
    links: Mapping[str, tuple[LinkView, ...]]
    included: tuple[Inclusion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": trimmed_card(self.task),
            "documents": [d.to_dict() for d in self.documents],
            "links": {kind: [v.to_dict() for v in views] for kind, views in self.links.items()},
            "included": [i.to_dict() for i in self.included],
        }


def trimmed_card(card: TaskCard) -> dict[str, Any]:
    """Card head + only the LATEST work_log entry — the token discipline
    inherited from the tracker's linked_context (HATS-681 argument). Link edges
    are NOT repeated here: they live in the top-level ``links`` object (HATS-1028)."""
    latest = card.work_log[-1] if card.work_log else None
    return {
        "id": card.id,
        "title": card.title,
        "state": card.state,
        "priority": card.priority,
        "tags": card.tags,
        "resolution": card.resolution or None,
        "description": card.description,
        "created": card.created,
        "updated": card.updated,
        "latest_work_log": latest.to_dict() if latest else None,
    }


def _doc_names_for(kind: LinkKind, registry: LinksRegistry) -> tuple[str, ...]:
    """Which outcome docs to surface per kind: the parent epic's plan vs the
    summary/retro of a dependency/relation; derived kinds carry no docs."""
    if kind.derived:
        return ()
    if kind is registry.hierarchy_kind:
        return PARENT_DOC_NAMES
    return LINKED_DOC_NAMES


def build_context(
    tasks_dir: Path,
    task_id: str,
    *,
    registry: LinksRegistry | None = None,
    selectors: Sequence[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ContextPackage:
    """Assemble the one-call discovery package for a task.

    Card + K2 document view + one ``links`` map (each configured kind → its
    target views: id, title, state, resolution, doc paths with mtime). Derived
    children come from the kernel's reverse scan. Dangling links are skipped
    (linked_context precedent); an id already shown under an earlier (more
    salient) kind is not repeated.
    """
    reg = registry if registry is not None else load_registry()
    card = _load_card(tasks_dir, task_id)
    if card is None:
        raise UnknownTaskError(task_id)
    documents = tuple(DocStore(tasks_dir).scan(task_id))

    kernel = Kernel(tasks_dir, registry=reg)
    derived: dict[str, list[str]] = {}
    children_kind = reg.children_kind
    if children_kind is not None:
        derived[children_kind.name] = kernel.children_of(task_id)
    resolved = resolve_links(reg, card, derived=derived)

    seen = {task_id}
    links: dict[str, tuple[LinkView, ...]] = {}
    for kind_name, ids in resolved.items():
        kind = reg.get(kind_name)
        if kind is None:
            continue
        doc_names = _doc_names_for(kind, reg)
        views: list[LinkView] = []
        for lid in ids:
            if not lid or lid in seen:
                continue
            seen.add(lid)
            view = _link_view(tasks_dir, kind_name, lid, doc_names)
            if view is not None:
                views.append(view)
        if views:
            links[kind_name] = tuple(views)

    flat_views = tuple(v for views in links.values() for v in views)
    included = _build_inclusions(tasks_dir, card, documents, flat_views, selectors, max_bytes)
    return ContextPackage(card, documents, links, included)


def _link_view(
    tasks_dir: Path, kind: str, task_id: str, doc_names: Sequence[str]
) -> LinkView | None:
    card = _load_card(tasks_dir, task_id)
    if card is None:
        return None
    docs: list[DocRef] = []
    for name in doc_names:
        path = tasks_dir / task_id / name
        if path.is_file():
            docs.append(DocRef(name, path.absolute(), _mtime_iso(path)))
    return LinkView(
        kind, card.id, card.title, card.state, card.priority, card.resolution, tuple(docs)
    )


def _build_inclusions(
    tasks_dir: Path,
    card: TaskCard,
    documents: Sequence[DocInfo],
    link_views: Sequence[LinkView],
    selectors: Sequence[str],
    max_bytes: int,
) -> tuple[Inclusion, ...]:
    """Resolve ``--with`` selectors. ``plan``/``summary`` skip missing files
    (conventional names may legitimately not exist yet); an explicit
    ``doc:<name>`` that resolves to nothing is a loud typed error."""
    out: list[Inclusion] = []
    seen: set[tuple[str, str]] = set()

    def add(owner: str, name: str, path: Path) -> None:
        if (owner, name) in seen or not path.is_file():
            return
        seen.add((owner, name))
        data = path.read_bytes()
        out.append(
            Inclusion(
                task_id=owner,
                name=name,
                path=path.absolute(),
                size=len(data),
                truncated=len(data) > max_bytes,
                content=data[:max_bytes].decode("utf-8", errors="replace"),
            )
        )

    for raw in selectors:
        sel = raw.strip()
        if sel == "plan":
            add(card.id, "plan.md", tasks_dir / card.id / "plan.md")
        elif sel == "summary":
            add(card.id, "summary.md", tasks_dir / card.id / "summary.md")
            for view in link_views:
                for ref in view.docs:
                    if ref.name == "summary.md":
                        add(view.id, ref.name, ref.path)
        elif sel.startswith("doc:"):
            name = sel[len("doc:") :]
            match = next((d for d in documents if d.name == name), None)
            if match is None or not match.path.is_file():
                raise UnknownDocumentError(card.id, name)
            add(card.id, name, match.path)
        elif sel:
            raise UnknownSelectorError(sel)
    return tuple(out)
