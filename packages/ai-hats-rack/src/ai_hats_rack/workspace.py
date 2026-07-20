"""Multi-backlog workspace resolver (HATS-1044, ADR-0017 §2).

N kernel instances, one per backlog (catalog + definition), under one thin
resolver; each :class:`Kernel` stays blind to the set (kernel slimming). Identity
is ``(root_id, name)`` — a workspace may mount several roots. Routing is by id
prefix (interview D): ``HYP-042`` -> the hypotheses kernel. Prefix uniqueness is
validated WITHIN a root at discovery; ACROSS roots a duplicate is legal and
unqualified routing raises :class:`AmbiguousPrefixError` demanding ``<root>:<id>``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .cardschema import build_card_schema
from .composition import compose_subscribers, stock_factories, stock_validators
from .definition import BacklogDefinition, load_backlog, resolve_definition
from .dispatch import Subscriber, bind_subscribers, validate_requires_states
from .errors import RackConfigError
from .events import LinkMirrorEvent
from .journal import JsonlJournalSink
from .kernel import Kernel
from .linked import card_exists
from .resolver import RackRoot

#: A root's short identity — the qualifier the CLI accepts as ``<root>:<id>``.
RootId = str

#: An ``<id>`` is ``<prefix>-<number>``; the prefix routes it to a backlog.
_ID_RE = re.compile(r"^(?P<prefix>.+)-\d+$")


class WorkspaceError(RackConfigError):
    """Base for workspace discovery / routing invariants — routed to the CLI
    ``internal`` marker with the rest of the RackConfigError subtree."""


class DuplicatePrefixError(WorkspaceError):
    """Two backlogs in ONE root claim the same id prefix — a load-time refusal
    (set-level uniqueness, interview D). Across roots a duplicate is legal."""

    def __init__(self, prefix: str, names: Sequence[str], root_id: str) -> None:
        self.prefix = prefix
        self.names = tuple(names)
        super().__init__(
            f"root {root_id!r}: prefix {prefix!r} is claimed by more than one "
            f"backlog {list(self.names)} — a prefix must be unique within a root"
        )


class UnknownPrefixError(WorkspaceError):
    """An id whose prefix matches no configured backlog — names the prefixes."""

    def __init__(self, prefix: str, configured: Sequence[str]) -> None:
        self.prefix = prefix
        self.configured = tuple(configured)
        super().__init__(
            f"no backlog for id prefix {prefix!r}: configured prefixes are "
            f"{list(self.configured)}"
        )


class AmbiguousPrefixError(WorkspaceError):
    """A prefix mounted from several roots, addressed unqualified — demands the
    ``<root>:<id>`` qualifier (or ``--root``); never a silent first-match."""

    def __init__(self, prefix: str, roots: Sequence[str]) -> None:
        self.prefix = prefix
        self.roots = tuple(roots)
        super().__init__(
            f"prefix {prefix!r} is mounted from several roots {list(self.roots)}; "
            f"qualify the id as <root>:<id> (e.g. {self.roots[0]}:{prefix}-1) or "
            "pass --root"
        )


class UnknownBacklogError(WorkspaceError):
    """``--backlog X`` names no mounted backlog (by ``cli_alias`` or ``name``) —
    names what is mounted, never a silent empty scan."""

    def __init__(self, name: str, mounted: Sequence[str]) -> None:
        self.name = name
        self.mounted = tuple(mounted)
        super().__init__(
            f"unknown backlog {name!r} — mounted: {', '.join(self.mounted) or '(none)'}"
        )


class UnknownExtensionError(WorkspaceError):
    """``Workspace.extension`` was asked for an extension no mounted backlog
    declares — names what is configured (the reach for step-6 consumers)."""

    def __init__(self, name: str, configured: Sequence[str]) -> None:
        self.extension_name = name
        self.configured = tuple(configured)
        super().__init__(
            f"no mounted backlog declares extension {name!r}; declared: {list(self.configured)}"
        )


class AmbiguousExtensionError(WorkspaceError):
    """An extension declared by mounts in several roots, addressed unqualified —
    demands the ``root`` qualifier; never a silent first-match."""

    def __init__(self, name: str, roots: Sequence[str]) -> None:
        self.extension_name = name
        self.roots = tuple(roots)
        super().__init__(
            f"extension {name!r} is declared in several roots {list(self.roots)}; "
            "pass root= to disambiguate"
        )


@dataclass(frozen=True)
class BacklogInstance:
    """One mounted backlog: identity ``(root_id, name)`` + its catalog and the
    immutable definition it runs on. ``is_tasks`` marks the default tasks
    catalog of its root — the one instance the integrator attaches its
    tasks-discipline code channel (ownership/worktree/epic) to."""

    root_id: RootId
    name: str
    prefix: str
    catalog: Path
    definition: BacklogDefinition
    is_tasks: bool = False


#: How a workspace turns an instance into a kernel. A builder MAY return ``None``
#: to fall through to the portable default (the integrator overrides only the
#: tasks instance; HYP/PROP get the portable kit + their declared extensions).
KernelBuilder = Callable[[BacklogInstance], "Kernel | None"]


@dataclass(frozen=True)
class Workspace:
    """Thin resolver over N backlog instances (ADR-0017 §2)."""

    instances: tuple[BacklogInstance, ...]
    #: integrator override for kernel construction; ``None`` (or a ``None``
    #: return) means the portable default builds the instance.
    kernel_builder: KernelBuilder | None = field(default=None, compare=False)

    # ----- discovery --------------------------------------------------------

    @classmethod
    def discover(
        cls,
        roots: Sequence[RackRoot],
        *,
        kernel_builder: KernelBuilder | None = None,
    ) -> "Workspace":
        """Per root: the default tasks catalog is ALWAYS an instance (packaged
        definition if it has no file, honoring the deprecated ``task_prefix``
        alias); then scan ``<ai_hats_dir>/tracker/**`` for sibling ``backlog.yaml``
        files. Prefix uniqueness is validated WITHIN each root (fail-closed)."""
        instances: list[BacklogInstance] = []
        for root in roots:
            root_id = root.project_dir.name or str(root.project_dir)
            tasks_defn = resolve_definition(
                root.tasks_dir, prefix_alias=root.prefix, project_dir=root.project_dir
            )
            here = [
                BacklogInstance(
                    root_id, tasks_defn.name, tasks_defn.prefix, root.tasks_dir, tasks_defn,
                    is_tasks=True,
                )
            ]
            for catalog, defn in _scan_sibling_backlogs(root.tasks_dir):
                here.append(
                    BacklogInstance(root_id, defn.name, defn.prefix, catalog, defn, is_tasks=False)
                )
            _check_prefix_uniqueness(here, root_id)
            instances.extend(here)
        return cls(instances=tuple(instances), kernel_builder=kernel_builder)

    # ----- routing ----------------------------------------------------------

    def instance_for(self, item_id: str, root: RootId | None = None) -> BacklogInstance:
        """Route an id to its backlog by prefix (ADR-0017 §2). Unknown prefix ->
        :class:`UnknownPrefixError`; a prefix mounted from several roots and left
        unqualified -> :class:`AmbiguousPrefixError`."""
        qual_root, bare = _split_qualifier(item_id)
        want_root = root or qual_root
        prefix = _prefix_of(bare)
        matches = [
            i
            for i in self.instances
            if i.prefix == prefix and (want_root is None or i.root_id == want_root)
        ]
        if not matches:
            raise UnknownPrefixError(prefix, sorted({i.prefix for i in self.instances}))
        if len(matches) > 1:
            raise AmbiguousPrefixError(prefix, sorted({i.root_id for i in matches}))
        return matches[0]

    def backlog_cli_names(self) -> tuple[str, ...]:
        """The CLI selector each mounted instance answers to — its ``cli_alias`` or,
        absent that, its ``name`` (the same token its per-backlog create group is
        named by, HATS-1036)."""
        return tuple((i.definition.cli_alias or i.name) for i in self.instances)

    def instance_by_name(self, name: str) -> BacklogInstance:
        """Route a backlog SELECTOR (``--backlog``) to its instance by CLI name —
        ``cli_alias`` or ``name``, matched dynamically over the mounted instances
        (open registry, HATS-1080). Unknown -> :class:`UnknownBacklogError`."""
        for i in self.instances:
            if name in (i.definition.cli_alias, i.name):
                return i
        raise UnknownBacklogError(name, self.backlog_cli_names())

    def kernel_for(self, item_id: str, root: RootId | None = None) -> Kernel:
        """The kernel of the backlog an id routes to — the integrator override
        first (tasks instance), else the portable kit (ADR-0017 §2/§4)."""
        return self.kernel_for_instance(self.instance_for(item_id, root))

    def kernel_for_instance(self, instance: BacklogInstance) -> Kernel:
        """The kernel of a mounted instance — the routing-free path a per-backlog
        ``create`` group takes (no id yet, HATS-1036): integrator override first
        (tasks instance), else the portable kit (ADR-0017 §2/§4)."""
        if self.kernel_builder is not None:
            kernel = self.kernel_builder(instance)
            if kernel is not None:
                return kernel
        return portable_kernel(instance, exists_checker=self._existence_checker_for(instance))

    def extension(self, name: str, *, root: RootId | None = None) -> Subscriber:
        """The bound ambient extension named ``name`` on the instance that declares
        it — the reach for its python API (``hyp-verdicts.append_verdict`` /
        ``.autoclose``, ``prop-votes.add_vote``). Unknown -> :class:`UnknownExtensionError`;
        declared in several roots and left unqualified -> :class:`AmbiguousExtensionError`."""
        matches = [
            i
            for i in self.instances
            if (root is None or i.root_id == root)
            and any(ref.name == name for ref in i.definition.bindings.extensions)
        ]
        if not matches:
            declared = sorted(
                {ref.name for i in self.instances for ref in i.definition.bindings.extensions}
            )
            raise UnknownExtensionError(name, declared)
        if len(matches) > 1:
            raise AmbiguousExtensionError(name, sorted({i.root_id for i in matches}))
        instance = matches[0]
        _kernel, subscribers = _compose_portable(
            instance, self._existence_checker_for(instance), None, None
        )
        for sub in subscribers:
            if sub.name == name:
                return sub
        raise UnknownExtensionError(name, [name])  # declared but not composed (unreachable)

    def exists(self, item_id: str) -> bool:
        """Cross-backlog existence: does a card with this id exist in the backlog
        its prefix routes to (ADR-0017 §2)? Unknown/foreign prefix -> ``False``
        (the caller raises its own not-found), never a routing exception."""
        qual_root, bare = _split_qualifier(item_id)
        prefix = _prefix_of(bare)
        for i in self.instances:
            if i.prefix == prefix and (qual_root is None or i.root_id == qual_root):
                if card_exists(i.catalog, bare):
                    return True
        return False

    # ----- mirror (post-lock, cross-backlog) --------------------------------

    def dispatch_mirror(
        self, event: LinkMirrorEvent, *, actor: str, caller_cwd: Path, root: RootId | None = None
    ) -> object:
        """Route a link mirror event to the TARGET backlog's kernel and apply it
        in a fresh lock window (ADR-0017 §2/R4) — the only workspace write path."""
        return self.kernel_for(event.target, root=root).apply_mirror(
            event, actor=actor, caller_cwd=caller_cwd
        )

    def mirror_after(
        self, origin_id: str, result: object, *, actor: str, caller_cwd: Path, root: RootId | None = None
    ) -> None:
        """After the origin's link/unlink persists, dispatch the mirror for each
        CHANGED stored-inverse link op (ADR-0017 §2/R4). Symmetric and derived-
        inverse kinds carry no mirror, so a tasks-only backlog is a no-op."""
        registry = self.instance_for(origin_id, root).definition.links_registry
        for op in getattr(result, "ops", ()):
            if op.get("op") not in ("link", "unlink") or not op.get("changed"):
                continue
            removed = op["op"] == "unlink"
            kind_names = op.get("kinds", []) if removed else [op.get("kind")]
            for kind_name in kind_names:
                kind = registry.get(kind_name) if kind_name else None
                if kind is None or not kind.inverse or kind.symmetric:
                    continue
                inverse = registry.get(kind.inverse)
                if inverse is None or inverse.derived:
                    continue
                self.dispatch_mirror(
                    LinkMirrorEvent(
                        kind=kind.inverse, origin=origin_id, target=op["target"], removed=removed
                    ),
                    actor=actor,
                    caller_cwd=caller_cwd,
                    root=root,
                )

    # ----- internals --------------------------------------------------------

    def _existence_checker_for(self, instance: BacklogInstance) -> "ExistenceChecker":
        """A cross-backlog target-existence checker bound to one instance: a
        kind's ``targets`` names the sibling backlog to look in (own catalog
        when unset). Injected into the portable kernel so in-lock handlers never
        need a workspace handle (one-lock rule)."""

        def check(target_id: str, targets: str | None) -> bool:
            if not targets:
                return card_exists(instance.catalog, target_id)
            for i in self.instances:
                if i.root_id == instance.root_id and i.name == targets:
                    return card_exists(i.catalog, target_id)
            return False

        return check


#: Kernel-side existence seam: ``(target_id, targets_backlog_or_None) -> bool``.
ExistenceChecker = Callable[[str, "str | None"], bool]


def _compose_portable(
    instance: BacklogInstance,
    exists_checker: "ExistenceChecker | None",
    factories: object,
    validators: object,
) -> tuple[Kernel, list[Subscriber]]:
    """The portable composition path (definition -> subscribers -> Kernel -> bind),
    reusing one factory/validator registry (ADR-0017 §4/§5). Returns the kernel AND
    its bound subscribers so a caller can reach an extension's python API."""
    defn = instance.definition
    catalog = instance.catalog
    facs = stock_factories() if factories is None else factories  # type: ignore[assignment]
    vals = stock_validators() if validators is None else validators  # type: ignore[assignment]
    subscribers: list[Subscriber] = compose_subscribers(defn, catalog, facs)
    validate_requires_states(subscribers, defn.topology, source=str(catalog))
    kernel = Kernel(
        catalog,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        edge_names=defn.edge_names,
        schema=build_card_schema(defn, vals),
        subscribers=subscribers,
        journal_sink=JsonlJournalSink(catalog),
        exists_checker=exists_checker,
    )
    bind_subscribers(subscribers, kernel)
    return kernel, subscribers


def portable_kernel(
    instance: BacklogInstance,
    *,
    exists_checker: "ExistenceChecker | None" = None,
    factories: object = None,
    validators: object = None,
) -> Kernel:
    """Build a kernel for one instance via the portable composition path (ADR-0017
    §4/§5). The integrator attaches its tasks-discipline code channel elsewhere; a
    HYP/PROP instance gets exactly this portable kit plus its declared extensions."""
    kernel, _subscribers = _compose_portable(instance, exists_checker, factories, validators)
    return kernel


# ----- discovery helpers ---------------------------------------------------

#: We only scan for siblings when tasks_dir keeps this tail, so an explicit
#: ``--tasks-dir`` override never walks an arbitrary tree.
_TASKS_TAIL = ("tracker", "backlog", "tasks")


def _scan_sibling_backlogs(tasks_dir: Path) -> list[tuple[Path, BacklogDefinition]]:
    """Walk ``<ai_hats_dir>/tracker`` for ``backlog.yaml`` files OTHER than the
    tasks catalog's, pruning the tasks subtree (cards live there) and never
    descending into a catalog's own card dirs. Returns ``(catalog, definition)``.
    """
    if tasks_dir.parts[-3:] != _TASKS_TAIL:
        return []  # non-conventional override -> the tasks instance stands alone
    tracker = tasks_dir.parent.parent
    if not tracker.is_dir():
        return []
    found: list[tuple[Path, BacklogDefinition]] = []
    for dirpath, dirnames, filenames in os.walk(tracker):
        here = Path(dirpath)
        if here == tasks_dir:
            dirnames[:] = []  # the tasks catalog is handled separately; skip its cards
            continue
        if "backlog.yaml" in filenames:
            found.append((here, load_backlog(here / "backlog.yaml")))
            dirnames[:] = []  # a backlog root is a leaf catalog — do not walk its cards
    found.sort(key=lambda cd: str(cd[0]))
    return found


def _check_prefix_uniqueness(instances: Sequence[BacklogInstance], root_id: RootId) -> None:
    by_prefix: dict[str, list[str]] = {}
    for inst in instances:
        by_prefix.setdefault(inst.prefix, []).append(inst.name)
    for prefix, names in by_prefix.items():
        if len(names) > 1:
            raise DuplicatePrefixError(prefix, names, root_id)


def _split_qualifier(item_id: str) -> tuple[RootId | None, str]:
    """``<root>:<id>`` -> ``(root, id)``; a bare id -> ``(None, id)`` (task ids
    never contain a colon, so the split is unambiguous)."""
    if ":" in item_id:
        root_id, _, bare = item_id.partition(":")
        return (root_id or None), bare
    return None, item_id


def _prefix_of(item_id: str) -> str:
    match = _ID_RE.match(item_id)
    return match.group("prefix") if match else item_id


__all__ = [
    "AmbiguousExtensionError",
    "AmbiguousPrefixError",
    "BacklogInstance",
    "DuplicatePrefixError",
    "ExistenceChecker",
    "KernelBuilder",
    "RootId",
    "UnknownExtensionError",
    "UnknownPrefixError",
    "Workspace",
    "WorkspaceError",
    "portable_kernel",
]
