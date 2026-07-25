"""Materialization port — the one seam every session write goes through (HATS-1211).

``apply`` writes and records; ``plan`` records the intent and touches nothing, so
the dry-run report cannot drift from reality — same call, two modes. Design and
the by-construction argument: tasks/HATS-1211/plan.md.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MaterializationMode(str, Enum):
    APPLY = "apply"
    PLAN = "plan"


class WriteKind(str, Enum):
    WRITE_TEXT = "write_text"
    COPY_TREE = "copy_tree"
    MERGE_JSON = "merge_json"
    REMOVE_TREE = "remove_tree"
    MKDIR = "mkdir"


@dataclass(frozen=True)
class MaterializationEntry:
    kind: WriteKind
    target: Path
    size: int = 0
    source: Path | None = None
    file_count: int = 1
    detail: str = ""


_CREATING = (WriteKind.WRITE_TEXT, WriteKind.COPY_TREE, WriteKind.MERGE_JSON)


@dataclass
class MaterializationPlan:
    entries: list[MaterializationEntry] = field(default_factory=list)

    def duplicates(self) -> list[Path]:
        """Targets materialized more than once — the same bytes produced twice.

        Only creating kinds count: a rebuild is legitimately remove-then-create
        on one path.
        """
        seen: dict[Path, int] = {}
        for entry in self.entries:
            if entry.kind in _CREATING:
                seen[entry.target] = seen.get(entry.target, 0) + 1
        return [target for target, n in seen.items() if n > 1]


def _key_diff(current_text: str | None, desired: dict) -> str:
    """Top-level key delta, rendered for the report (``+hooks``, ``~theme``)."""
    try:
        current = json.loads(current_text) if current_text else {}
    except ValueError:
        current = {}
    if not isinstance(current, dict):
        current = {}

    added = [f"+{k}" for k in desired if k not in current]
    changed = [f"~{k}" for k in desired if k in current and current[k] != desired[k]]
    removed = [f"-{k}" for k in current if k not in desired]
    return " ".join(added + changed + removed)


class Materializer:
    """The write port. Construct per session build; read ``plan`` afterwards."""

    def __init__(self, mode: MaterializationMode = MaterializationMode.APPLY) -> None:
        self.mode = MaterializationMode(mode)
        self.plan = MaterializationPlan()

    @property
    def _writes(self) -> bool:
        return self.mode is MaterializationMode.APPLY

    def write_text(self, path: Path, content: str) -> None:
        if self._writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.plan.entries.append(
            MaterializationEntry(
                kind=WriteKind.WRITE_TEXT,
                target=path,
                size=len(content.encode()),
            )
        )

    def mkdir(self, path: Path, *, parents: bool = True, exist_ok: bool = True) -> None:
        """Only a real creation is recorded — every category handler mkdirs the cache."""
        if path.is_dir():
            return
        if self._writes:
            path.mkdir(parents=parents, exist_ok=exist_ok)
        self.plan.entries.append(
            MaterializationEntry(kind=WriteKind.MKDIR, target=path, file_count=0)
        )

    def copy_tree(self, src: Path, dest: Path) -> None:
        """Recursive copy. Plan mode sizes the source instead of performing it."""
        files = [p for p in src.rglob("*") if p.is_file()]
        if self._writes:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
        self.plan.entries.append(
            MaterializationEntry(
                kind=WriteKind.COPY_TREE,
                target=dest,
                source=src,
                size=sum(p.stat().st_size for p in files),
                file_count=len(files),
            )
        )

    def merge_json(self, path: Path, data: dict) -> bool:
        """Write ``data`` as JSON only if it differs from what is on disk.

        The caller hands the whole desired document; content decides. Returns
        whether it differs, so read-modify-write callers keep their ``changed``
        semantics. Used for user-owned files (agy global settings), hence the
        recorded key-level diff — a session touching a user's file is news.
        """
        content = json.dumps(data, indent=2) + "\n"
        current = path.read_text() if path.is_file() else None
        if current == content:
            return False

        if self._writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.plan.entries.append(
            MaterializationEntry(
                kind=WriteKind.MERGE_JSON,
                target=path,
                size=len(content.encode()),
                detail=_key_diff(current, data),
            )
        )
        return True

    def remove_tree(self, path: Path) -> None:
        """Recursive delete. Absent target is a no-op and stays out of the plan."""
        if not path.exists():
            return
        if self._writes:
            shutil.rmtree(path)  # safe-delete: ok caller-owned session artifact
        self.plan.entries.append(
            MaterializationEntry(kind=WriteKind.REMOVE_TREE, target=path)
        )
