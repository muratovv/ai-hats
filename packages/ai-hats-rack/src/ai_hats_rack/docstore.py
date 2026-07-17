"""fs-as-truth doc store over ``tasks/<ID>/`` (HATS-1021, K2 of epic HATS-1014).

Rev4: writing a file into the task directory IS the registration — no other
write path exists. The ledger is a VIEW (live scan + digests on the fly);
task.yaml persists only frozen pins ``{name, digest, frozen}`` via the K1
model's ``extras``. Discovery, not injection: names + absolute paths + mtime,
never content (the 210K-character baseline F4 lesson).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from .kernel import LOCK_TIMEOUT, LockTimeoutError, UnknownTaskError
from .models import TaskCard, utc_now

DIGEST_LEN = 12  # truncated SHA-256 — the HATS-402 attachment precedent
DIGEST_PREFIX = "sha256:"
PINS_FIELD = "documents"
#: files that are the anchor/machinery, never documents. Dotfiles (.lock,
#: editor droppings) are excluded by the leading-dot rule in _is_document.
SERVICE_FILES = frozenset({"task.yaml"})

DRIFT_MODIFIED = "modified"
DRIFT_MISSING = "missing"


class DocumentNameError(Exception):
    """The name cannot address a document inside the task directory."""

    def __init__(self, name: str, why: str) -> None:
        self.name = name
        super().__init__(f"Invalid document name {name!r}: {why}")


class UnknownDocumentError(Exception):
    def __init__(self, task_id: str, name: str) -> None:
        self.task_id = task_id
        self.name = name
        super().__init__(f"No document '{name}' in tasks/{task_id}/")


class FrozenDocumentError(Exception):
    """Tiered escape hatch (PROP-035/063/064): the refusal names the flag."""

    def __init__(self, task_id: str, name: str) -> None:
        self.task_id = task_id
        self.name = name
        super().__init__(
            f"Document '{name}' of {task_id} is frozen. Pass --ack-frozen to "
            "remove it together with its pin (the file is still moved to "
            "trash, not deleted)."
        )


class FrozenPinDriftError(Exception):
    """Freezing over a pin whose file changed requires explicit --refreeze."""

    def __init__(self, task_id: str, name: str, pinned: str, current: str) -> None:
        self.task_id = task_id
        self.name = name
        self.pinned = pinned
        self.current = current
        super().__init__(
            f"Document '{name}' of {task_id} changed under its frozen pin "
            f"(pinned {pinned}, on disk {current}). Pass --refreeze to accept "
            "the new content as the frozen evidence."
        )


@dataclass(frozen=True)
class DocInfo:
    """One row of the live view; ``path`` is always absolute (discovery)."""

    name: str  # POSIX-relative inside tasks/<ID>/
    path: Path
    mtime: str  # UTC ISO-8601 Z; "" for a missing pinned file
    size: int
    digest: str  # sha256:<12 hex>; "" for a missing pinned file
    frozen: bool
    pinned_digest: str = ""
    drift: str = ""  # "", "modified", "missing"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "path": str(self.path),
            "mtime": self.mtime or None,
            "size": self.size,
            "digest": self.digest,
            "frozen": self.frozen,
            "drift": self.drift or None,
        }
        if self.frozen:
            d["pinned_digest"] = self.pinned_digest
        return d


@dataclass(frozen=True)
class RemoveResult:
    name: str
    trashed_to: Path | None  # None: no file on disk (dangling pin cleanup)
    pin_removed: bool


def compute_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return DIGEST_PREFIX + h.hexdigest()[:DIGEST_LEN]


def _mtime_iso(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_document(rel_parts: tuple[str, ...]) -> bool:
    if any(part.startswith(".") for part in rel_parts):
        return False
    return "/".join(rel_parts) not in SERVICE_FILES


def _require_valid_name(name: str) -> None:
    if not name:
        raise DocumentNameError(name, "empty")
    p = Path(name)
    if p.is_absolute():
        raise DocumentNameError(name, "must be relative to the task directory")
    if ".." in p.parts:
        raise DocumentNameError(name, "must not escape the task directory")
    if not _is_document(p.parts):
        raise DocumentNameError(name, "dotfiles and task.yaml are not documents")


class DocStore:
    """View + pin operations over one tasks dir (same layout the kernel uses)."""

    def __init__(self, tasks_dir: Path, *, lock_timeout: float = LOCK_TIMEOUT) -> None:
        self.tasks_dir = tasks_dir
        self._lock_timeout = lock_timeout

    # ----- view (the ledger) ------------------------------------------------

    def card_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def scan(self, task_id: str) -> list[DocInfo]:
        """Live view: directory scan + on-the-fly digests + pin verification.

        Verification is internal by design (no public ``verify`` verb): every
        ls/show runs it, so a file changed or lost under a frozen pin shows up
        as ``drift`` on the next look, not on a ritual command nobody runs.
        """
        return self._view(task_id, self._load_card(task_id))

    def _load_card(self, task_id: str) -> TaskCard:
        path = self.card_dir(task_id) / "task.yaml"
        if not path.exists():
            raise UnknownTaskError(task_id)
        return TaskCard.from_yaml(path)

    def _view(self, task_id: str, card: TaskCard) -> list[DocInfo]:
        card_dir = self.card_dir(task_id)
        pins = {p["name"]: str(p.get("digest", "")) for p in self._pins(card)}
        rows: dict[str, DocInfo] = {}
        if card_dir.is_dir():
            for path in sorted(card_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(card_dir).parts
                if not _is_document(rel):
                    continue
                name = "/".join(rel)
                digest = compute_digest(path)
                pinned = pins.get(name, "")
                rows[name] = DocInfo(
                    name=name,
                    path=path.absolute(),
                    mtime=_mtime_iso(path),
                    size=path.stat().st_size,
                    digest=digest,
                    frozen=name in pins,
                    pinned_digest=pinned,
                    drift=DRIFT_MODIFIED if pinned and pinned != digest else "",
                )
        for name, pinned in pins.items():
            if name not in rows:  # pin without a blob: loud, not silently gone
                rows[name] = DocInfo(
                    name=name,
                    path=(card_dir / name).absolute(),
                    mtime="",
                    size=0,
                    digest="",
                    frozen=True,
                    pinned_digest=pinned,
                    drift=DRIFT_MISSING,
                )
        return [rows[name] for name in sorted(rows)]

    @staticmethod
    def _pins(card: TaskCard) -> list[dict[str, Any]]:
        raw = card.extras.get(PINS_FIELD)
        if not isinstance(raw, list):
            return []
        return [p for p in raw if isinstance(p, dict) and p.get("name")]

    # ----- pin mutations (the only task.yaml writes K2 owns) -----------------

    def freeze(
        self, task_id: str, name: str, *, actor: str = "", refreeze: bool = False
    ) -> DocInfo:
        """Pin ``{name, digest, frozen}`` into task.yaml (atomic, task-locked).

        Idempotent on an unchanged file; re-freezing changed content demands
        ``refreeze`` — evidence must not drift silently under its pin.
        """
        _require_valid_name(name)

        def op(card: TaskCard) -> tuple[DocInfo, bool]:
            path = self.card_dir(task_id) / name
            if not path.is_file():
                raise UnknownDocumentError(task_id, name)
            digest = compute_digest(path)
            pins = self._pins(card)
            entry = next((p for p in pins if p["name"] == name), None)
            if entry is not None:
                pinned = str(entry.get("digest", ""))
                if pinned == digest:
                    return self._info(task_id, name, path, digest), False
                if not refreeze:
                    raise FrozenPinDriftError(task_id, name, pinned, digest)
                entry["digest"] = digest
                card.log_work(f"Re-froze document {name} ({pinned} → {digest})", actor=actor)
            else:
                pins.append({"name": name, "digest": digest, "frozen": True})
                card.log_work(f"Froze document {name} ({digest})", actor=actor)
            card.extras[PINS_FIELD] = pins
            return self._info(task_id, name, path, digest), True

        return self._locked_card_op(task_id, op)

    def remove(
        self, task_id: str, name: str, *, actor: str = "", ack_frozen: bool = False
    ) -> RemoveResult:
        """Delete-policy: the file moves to a trash session (HATS-470 pattern),
        never vanishes; a frozen pin additionally demands ``ack_frozen``."""
        _require_valid_name(name)

        def op(card: TaskCard) -> tuple[RemoveResult, bool]:
            path = self.card_dir(task_id) / name
            pins = self._pins(card)
            entry = next((p for p in pins if p["name"] == name), None)
            if entry is not None and not ack_frozen:
                raise FrozenDocumentError(task_id, name)
            if entry is None and not path.is_file():
                raise UnknownDocumentError(task_id, name)
            trashed_to = _trash(path, task_id, name) if path.is_file() else None
            if entry is not None:
                pins.remove(entry)
                if pins:
                    card.extras[PINS_FIELD] = pins
                else:  # keep pinless cards byte-clean: no empty documents: []
                    card.extras.pop(PINS_FIELD, None)
            where = f" (recoverable: {trashed_to})" if trashed_to else " (no file on disk)"
            frozen_note = "frozen " if entry is not None else ""
            card.log_work(f"Removed {frozen_note}document {name}{where}", actor=actor)
            return RemoveResult(name, trashed_to, pin_removed=entry is not None), True

        return self._locked_card_op(task_id, op)

    def _info(self, task_id: str, name: str, path: Path, digest: str) -> DocInfo:
        return DocInfo(
            name=name,
            path=path.absolute(),
            mtime=_mtime_iso(path),
            size=path.stat().st_size,
            digest=digest,
            frozen=True,
            pinned_digest=digest,
        )

    def _locked_card_op(self, task_id: str, op):
        """Load → mutate → single atomic persist, inside the task lock — the
        same transaction window the kernel uses (lock model §2.2)."""
        lock_path = self.card_dir(task_id) / ".lock"
        if not (self.card_dir(task_id) / "task.yaml").exists():
            raise UnknownTaskError(task_id)
        lock = FileLock(str(lock_path), timeout=self._lock_timeout)
        try:
            with lock:
                card = self._load_card(task_id)
                result, dirty = op(card)
                if dirty:
                    card.updated = utc_now()
                    card.save(self.card_dir(task_id) / "task.yaml")
        except Timeout as exc:
            raise LockTimeoutError(lock_path, f"doc op on {task_id}", self._lock_timeout) from exc
        return result


def _trash(path: Path, task_id: str, name: str) -> Path:
    """Move the victim into a fresh trash session under $TMPDIR (HATS-470:
    destructive ops stay recoverable; OS tmp cleanup owns retention)."""
    base = Path(tempfile.gettempdir()) / "ai-hats-rack"
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session = Path(tempfile.mkdtemp(prefix=f"trash-{ts}-{os.getpid()}-", dir=base))
    dest = session / task_id / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest
