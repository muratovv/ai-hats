"""Ordered composite-transition ops (HATS-1030): the single mutating verb.

``rack transition <ID>`` runs an ORDERED sequence of ops under ONE task lock with
a single card persist (K1). Flag order = execution order; effects of earlier ops
are visible to later ops' handlers because file-touching ops materialize into
``tasks/<ID>/`` immediately, backed by an undo stack — so an abort of any op rolls
back the WHOLE sequence, files included. The kernel owns the lock/persist/edge
dispatch; this module owns the op vocabulary, the argv-order parser, and the
lock-free executors for the non-state ops.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, Union

from .docstore import _require_valid_name, freeze_on_card, remove_on_card
from .errors import RackError
from .kernel import UnknownTaskError
from .linked import link_on_card, unlink_on_card
from .models import TaskCard
from .registry import LinksRegistry


class OpParseError(RackError):
    """A composite-transition argv token stream could not be parsed."""


class AttachSourceError(RackError):
    """``--attach`` names a source path that is not a readable file."""

    def __init__(self, src: str) -> None:
        self.src = src
        super().__init__(f"Attach source {src!r} is not a file")


# ----- op vocabulary ----------------------------------------------------------


@dataclass(frozen=True)
class StateOp:
    to_state: str


@dataclass(frozen=True)
class AttachOp:
    src: str
    name: str  # resolved doc name (basename or the explicit src:name part)


@dataclass(frozen=True)
class FreezeOp:
    name: str


@dataclass(frozen=True)
class RmOp:
    name: str


@dataclass(frozen=True)
class LogOp:
    message: str


@dataclass(frozen=True)
class LinkOp:
    kind: str
    target: str


@dataclass(frozen=True)
class UnlinkOp:
    kind: str | None
    target: str


Op = Union[StateOp, AttachOp, FreezeOp, RmOp, LogOp, LinkOp, UnlinkOp]

#: the complete op-kind vocabulary emitted into KernelResult.ops — "state" is
#: the kernel's (behind the FSM guard), the rest are the _EXECUTORS below. The
#: CLI op-echo renderer map (cli._OP_RENDERERS) is pinned exhaustive over this.
OP_KINDS: frozenset[str] = frozenset(
    {"state", "attach", "freeze", "rm", "log", "link", "unlink"}
)


# ----- argv-order parser ------------------------------------------------------


def _split_attach(spec: str) -> tuple[str, str]:
    """``src`` → (src, basename); ``src:name`` → (src, name). Splits on the LAST
    colon only when the right side is a bare name (no ``/``), so absolute POSIX
    paths pass through untouched."""
    if ":" in spec:
        src, _, name = spec.rpartition(":")
        if src and name and "/" not in name:
            return src, name
    return spec, Path(spec).name


def _split_edge(spec: str) -> tuple[str | None, str]:
    """``kind:id`` → (kind, id); bare ``id`` → (None, id)."""
    if ":" in spec:
        kind, _, target = spec.partition(":")
        return (kind or None), target
    return None, spec


#: op flag → number of value tokens it consumes (all consume exactly one).
_OP_FLAGS = frozenset(
    {"--state", "--attach", "--freeze", "--rm", "--log", "--link", "--unlink"}
)


def parse_ops(tokens: Sequence[str]) -> list[Op]:
    """Parse the ordered op-token stream into typed ops, PRESERVING argv order.

    Click cannot preserve the interleaving of distinct repeated options, so the
    composite-transition command captures the op tokens verbatim (unprocessed)
    and hands them here. A single leading bare token is the old-form sugar
    (``transition <ID> <state>`` == ``--state <state>``).
    """
    ops: list[Op] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in _OP_FLAGS:
            if i + 1 >= n:
                raise OpParseError(f"{tok} expects a value")
            value = tokens[i + 1]
            i += 2
            if tok == "--state":
                ops.append(StateOp(value))
            elif tok == "--attach":
                src, name = _split_attach(value)
                ops.append(AttachOp(src, name))
            elif tok == "--freeze":
                ops.append(FreezeOp(value))
            elif tok == "--rm":
                ops.append(RmOp(value))
            elif tok == "--log":
                ops.append(LogOp(value))
            elif tok == "--link":
                kind, target = _split_edge(value)
                ops.append(LinkOp(kind or "related", target))
            else:  # --unlink
                kind, target = _split_edge(value)
                ops.append(UnlinkOp(kind, target))
        elif not tok.startswith("-") and not ops and i == 0:
            ops.append(StateOp(tok))  # old-form sugar: a single positional state
            i += 1
        else:
            raise OpParseError(
                f"unexpected token {tok!r}; expected one of "
                "--state/--attach/--freeze/--rm/--log/--link/--unlink"
            )
    return ops


# ----- transaction context + executors ---------------------------------------


@dataclass
class OpTxn:
    """Per-transition scratch state shared across ops under the single lock."""

    task_id: str
    card: TaskCard
    card_dir: Path
    caller_cwd: Path
    registry: LinksRegistry
    actor: str
    ack_frozen: bool = False
    undo: list[Callable[[], None]] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    #: in-lock link/unlink dispatch hook (kernel-supplied): ``(kind, target,
    #: removed)`` fires ``link:<kind>``/``unlink:<kind>`` (HATS-1043 §3). None on
    #: the lock-free/test path — link ops then mutate without dispatching.
    dispatch_link: Callable[[str, str, bool], None] | None = None
    #: cross-backlog target-existence seam (kernel-supplied, ADR-0017 §2): ``(target,
    #: targets_backlog|None) -> bool``. None -> catalog-local (today's behavior).
    exists: Callable[[str, "str | None"], bool] | None = None

    def rollback(self) -> None:
        """Unwind file mutations in REVERSE registration order (abort path)."""
        while self.undo:
            self.undo.pop()()


def _apply_attach(txn: OpTxn, op: AttachOp) -> None:
    _require_valid_name(op.name)
    src_path = Path(op.src)
    if not src_path.is_absolute():
        src_path = txn.caller_cwd / src_path
    if not src_path.is_file():
        raise AttachSourceError(op.src)
    data = src_path.read_bytes()
    dest = txn.card_dir / op.name
    overwrote = dest.exists()
    if overwrote:
        original = dest.read_bytes()
        txn.undo.append(lambda: dest.write_bytes(original))
    else:
        # rollback of a file this transaction itself just created (abort path)
        txn.undo.append(lambda: dest.unlink(missing_ok=True))  # safe-delete: ok abort rollback
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    note = " (overwrote)" if overwrote else ""
    txn.card.log_work(f"Attached {op.name}{note}", actor=txn.actor)
    txn.results.append(
        {"op": "attach", "name": op.name, "path": str(dest.absolute()), "overwrote": overwrote}
    )


def _apply_freeze(txn: OpTxn, op: FreezeOp) -> None:
    # --ack-frozen doubles as the re-freeze hatch: the tiered "I know it is
    # frozen" flag covers accepting drifted content too (HATS-1031 Р13 recipe).
    info, changed = freeze_on_card(
        txn.card, txn.card_dir, op.name, actor=txn.actor, refreeze=txn.ack_frozen
    )
    txn.results.append(
        {"op": "freeze", "name": op.name, "digest": info.digest, "changed": changed}
    )


def _apply_rm(txn: OpTxn, op: RmOp) -> None:
    result, _ = remove_on_card(
        txn.card, txn.card_dir, op.name, actor=txn.actor, ack_frozen=txn.ack_frozen
    )
    if result.trashed_to is not None:
        trashed_to = result.trashed_to
        dest = txn.card_dir / op.name

        def _restore() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trashed_to), str(dest))

        txn.undo.append(_restore)
    revert = (
        f"rack transition {txn.task_id} --attach {result.trashed_to}:{op.name}"
        if result.trashed_to is not None
        else None
    )
    txn.results.append(
        {
            "op": "rm",
            "name": op.name,
            "trashed_to": str(result.trashed_to) if result.trashed_to else None,
            "pin_removed": result.pin_removed,
            "revert": revert,
        }
    )


def _apply_log(txn: OpTxn, op: LogOp) -> None:
    txn.card.log_work(op.message, actor=txn.actor)
    txn.results.append({"op": "log", "message": op.message})


def _apply_link(txn: OpTxn, op: LinkOp) -> None:
    # Route target existence through the workspace seam by the kind's `targets`
    # (ADR-0017 §2); a None-tolerant kind lookup keeps the pre-existing order —
    # existence before link_on_card's own kind/derived/self-link validation.
    kind = txn.registry.get(op.kind)
    targets = kind.targets if (kind is not None and kind.targets) else None
    exists = txn.exists or (lambda tid, _t: (txn.card_dir.parent / tid / "task.yaml").exists())
    if not exists(op.target, targets):
        raise UnknownTaskError(op.target)
    result = link_on_card(txn.registry, txn.card, op.target, op.kind, actor=txn.actor)
    if result.changed and txn.dispatch_link is not None:
        # In-lock, card already mutated: a declared handler sees the new link
        # and may abort before persist (rolls back the whole txn).
        txn.dispatch_link(result.kinds[0], op.target, False)
    txn.results.append(
        {
            "op": "link",
            "kind": result.kinds[0] if result.kinds else op.kind,
            "target": op.target,
            "changed": result.changed,
        }
    )


def _apply_unlink(txn: OpTxn, op: UnlinkOp) -> None:
    result = unlink_on_card(txn.registry, txn.card, op.target, op.kind, actor=txn.actor)
    if result.changed and txn.dispatch_link is not None:
        for kind in result.kinds:  # bare unlink removes every stored kind
            txn.dispatch_link(kind, op.target, True)
    revert = (
        f"rack transition {txn.task_id} --link {result.kinds[0]}:{op.target}"
        if result.changed
        else None
    )
    txn.results.append(
        {
            "op": "unlink",
            "target": op.target,
            "kinds": list(result.kinds),
            "changed": result.changed,
            "revert": revert,
        }
    )


_EXECUTORS: dict[type, Callable[[OpTxn, Op], None]] = {
    AttachOp: _apply_attach,
    FreezeOp: _apply_freeze,
    RmOp: _apply_rm,
    LogOp: _apply_log,
    LinkOp: _apply_link,
    UnlinkOp: _apply_unlink,
}


def apply_non_state_op(txn: OpTxn, op: Op) -> None:
    """Execute one non-state op against the shared txn (StateOp is the kernel's,
    since it needs the FSM guard + two-phase dispatch)."""
    executor = _EXECUTORS.get(type(op))
    if executor is None:  # pragma: no cover — StateOp is handled by the kernel
        raise OpParseError(f"no executor for op {op!r}")
    executor(txn, op)
