"""Domain logic for task attachments (HATS-402).

Pure helpers — no Click, no TaskStore, no logging. The CLI layer
(``ai_hats.cli.attach``) wraps these and applies side effects.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ai_hats_core import scrubbed_git_env

from .models import Attachment, TaskCard

DIGEST_LEN = 12  # truncated SHA-256 (48 bits) — see HATS-402 plan §1.


class ReconcileAction(str, Enum):
    """Outcome of ``reconcile`` — what should happen on disk + in the manifest."""

    ADDED = "added"
    """No prior entry, blob lives outside attachments/ → move + add entry."""

    REGISTERED_EXISTING = "registered_existing"
    """No prior entry, blob already inside attachments/ → no file-op, add entry.
    This covers legacy folders like HATS-213/attachments/plan.md."""

    NOOP = "noop"
    """Entry already present and digest matches → nothing to do."""

    ERROR_COLLISION = "error_collision"
    """Entry with the same name exists but digest differs → CLI must reject."""


class FileOp(str, Enum):
    MOVE = "move"
    NONE = "none"


@dataclass(frozen=True)
class ReconcileResult:
    action: ReconcileAction
    attachment: Attachment | None  # the entry to add/keep; None for ERROR_COLLISION
    file_op: FileOp
    existing_digest: str = ""  # populated for ERROR_COLLISION
    new_digest: str = ""  # populated for ERROR_COLLISION
    message: str = ""


class DivergenceKind(str, Enum):
    BLOB_WITHOUT_ENTRY = "+"  # file on disk, no manifest record
    ENTRY_WITHOUT_BLOB = "-"  # manifest record, no file
    DIGEST_DRIFT = "~"  # both present, but digest disagrees


@dataclass(frozen=True)
class Divergence:
    kind: DivergenceKind
    name: str


def compute_digest(path: Path) -> str:
    """Return the first ``DIGEST_LEN`` hex chars of the file's SHA-256."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:DIGEST_LEN]


def attachments_dir(card_dir: Path) -> Path:
    """Canonical attachments folder for a task card directory."""
    return card_dir / "attachments"


def is_binary(path: Path, peek: int = 4096) -> bool:
    """Cheap binary-detection: NUL byte in the first ``peek`` bytes."""
    with path.open("rb") as f:
        chunk = f.read(peek)
    return b"\x00" in chunk


def is_git_tracked(path: Path) -> bool:
    """Return True iff ``path`` is tracked by the surrounding git repo.

    Uncommitted-but-staged files count as untracked here — matches git's own
    view and forces explicit ``--yes`` for unrecoverable removals.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=path.parent,
            capture_output=True,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconcile(
    card: TaskCard,
    card_dir: Path,
    blob_path: Path,
    name: str,
    note: str = "",
) -> ReconcileResult:
    """Compute the action needed to attach ``blob_path`` as ``name`` on ``card``.

    Pure: does not touch disk beyond reading ``blob_path``. The CLI applies the
    recommended ``file_op`` and writes the manifest. ``card_dir`` is the path
    to the task card's directory (the one that contains ``attachments/``).
    """
    new_digest = compute_digest(blob_path)
    target_dir = attachments_dir(card_dir)
    existing = next((a for a in card.attachments if a.name == name), None)

    if existing is not None:
        if existing.digest == new_digest:
            return ReconcileResult(
                action=ReconcileAction.NOOP,
                attachment=existing,
                file_op=FileOp.NONE,
                message=f"already attached, digest matches ({existing.digest})",
            )
        return ReconcileResult(
            action=ReconcileAction.ERROR_COLLISION,
            attachment=None,
            file_op=FileOp.NONE,
            existing_digest=existing.digest,
            new_digest=new_digest,
            message=(
                f"name {name!r} already attached with different content "
                f"(existing digest: {existing.digest}, new: {new_digest})"
            ),
        )

    blob_already_inside = (
        blob_path.is_file()
        and blob_path.resolve().parent == target_dir.resolve()
    )
    new_entry = Attachment(
        name=name,
        digest=new_digest,
        added=_now_iso(),
        note=note,
    )
    if blob_already_inside:
        return ReconcileResult(
            action=ReconcileAction.REGISTERED_EXISTING,
            attachment=new_entry,
            file_op=FileOp.NONE,
        )
    return ReconcileResult(
        action=ReconcileAction.ADDED,
        attachment=new_entry,
        file_op=FileOp.MOVE,
    )


def verify_manifest(card: TaskCard, card_dir: Path) -> list[Divergence]:
    """Compare the on-disk ``attachments/`` folder against ``card.attachments``.

    Returns a list of divergences. Empty list = manifest and folder agree.
    """
    out: list[Divergence] = []
    target_dir = attachments_dir(card_dir)
    entries_by_name = {a.name: a for a in card.attachments}
    blobs_by_name: dict[str, Path] = {}
    if target_dir.is_dir():
        for child in target_dir.iterdir():
            if child.is_file():
                blobs_by_name[child.name] = child

    for name, blob in blobs_by_name.items():
        entry = entries_by_name.get(name)
        if entry is None:
            out.append(Divergence(DivergenceKind.BLOB_WITHOUT_ENTRY, name))
            continue
        if compute_digest(blob) != entry.digest:
            out.append(Divergence(DivergenceKind.DIGEST_DRIFT, name))

    for name in entries_by_name:
        if name not in blobs_by_name:
            out.append(Divergence(DivergenceKind.ENTRY_WITHOUT_BLOB, name))

    return out
