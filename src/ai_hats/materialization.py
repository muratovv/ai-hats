"""Materialization port — the one seam every session write goes through (HATS-1211).

One interface, two implementations: :class:`ApplyMaterializer` writes and records,
:class:`PlanMaterializer` records the same entry and touches nothing (``--dry-run``).
Both build their entries from the shared ``describe_*`` functions, so the record
cannot differ by implementation — that identity is the contract test suite's subject.
"""

from __future__ import annotations

import abc
import contextlib
import hashlib
import json
import shutil
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from .fs_digest import dir_digest

# A rebuild is a sub-second fs op, so a timeout means a stuck/dead holder.
LOCK_TIMEOUT = 30.0


class WriteKind(str, Enum):
    WRITE_TEXT = "write_text"
    WRITE_EXECUTABLE = "write_executable"
    COPY_TREE = "copy_tree"
    SYMLINK = "symlink"
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
    digest: str | None = None


_CREATING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.COPY_TREE,
    WriteKind.SYMLINK,
    WriteKind.MERGE_JSON,
)


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


# --- describe: pure, shared by both implementations so records cannot diverge ---


def describe_write_text(path: Path, content: str) -> MaterializationEntry:
    content_bytes = content.encode()
    return MaterializationEntry(
        kind=WriteKind.WRITE_TEXT,
        target=path,
        size=len(content_bytes),
        digest=hashlib.sha256(content_bytes).hexdigest(),
    )


def describe_write_executable(path: Path, content: str) -> MaterializationEntry:
    return replace(describe_write_text(path, content), kind=WriteKind.WRITE_EXECUTABLE)


def describe_copy_tree(src: Path, dest: Path) -> MaterializationEntry:
    files = [p for p in src.rglob("*") if p.is_file()] if src.is_dir() else []
    return MaterializationEntry(
        kind=WriteKind.COPY_TREE,
        target=dest,
        source=src,
        size=sum(p.stat().st_size for p in files),
        file_count=len(files),
        digest=dir_digest(src) if src.is_dir() else None,
    )


def describe_symlink(src: Path, dest: Path) -> MaterializationEntry:
    return MaterializationEntry(
        kind=WriteKind.SYMLINK,
        target=dest,
        source=src,
        size=0,
        file_count=0,
    )


def describe_mkdir(path: Path) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.MKDIR, target=path, file_count=0)


def describe_remove_tree(path: Path) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.REMOVE_TREE, target=path)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def describe_merge_json(path: Path, data: dict) -> MaterializationEntry:
    content = render_json(data)
    content_bytes = content.encode()
    current = path.read_text() if path.is_file() else None
    return MaterializationEntry(
        kind=WriteKind.MERGE_JSON,
        target=path,
        size=len(content_bytes),
        detail=_key_diff(current, data),
        digest=hashlib.sha256(content_bytes).hexdigest(),
    )


def json_differs(path: Path, data: dict) -> bool:
    current = path.read_text() if path.is_file() else None
    return current != render_json(data)


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


# --- the port ---


class Materializer(abc.ABC):
    """Every session write goes through here. Read ``plan`` after the build."""

    def __init__(self, *, lock_timeout: float = LOCK_TIMEOUT) -> None:
        self.plan = MaterializationPlan()
        self._lock_timeout = lock_timeout

    def _record(self, entry: MaterializationEntry) -> None:
        self.plan.entries.append(entry)

    @abc.abstractmethod
    def write_text(self, path: Path, content: str) -> None: ...

    @abc.abstractmethod
    def write_private_text(self, path: Path, content: str) -> None:
        """Write with owner-only permissions, recording no credential digest."""

    @abc.abstractmethod
    def write_executable(self, path: Path, content: str) -> None: ...

    @abc.abstractmethod
    def mkdir(self, path: Path) -> None:
        """Only a real creation is recorded — every category handler mkdirs the cache."""

    @abc.abstractmethod
    def copy_tree(self, src: Path, dest: Path) -> None: ...

    @abc.abstractmethod
    def symlink(self, src: Path, dest: Path) -> None: ...

    @abc.abstractmethod
    def merge_json(self, path: Path, data: dict) -> bool:
        """Write ``data`` as JSON only if it differs; returns whether it does.

        The caller hands the whole desired document; content decides, so
        read-modify-write callers keep their ``changed`` semantics.
        """

    @abc.abstractmethod
    def remove_tree(self, path: Path) -> None:
        """Recursive delete. Absent target is a no-op and stays out of the plan."""

    @abc.abstractmethod
    def lock(self, path: Path) -> contextlib.AbstractContextManager[None]:
        """Serialise a multi-step rebuild. Not materialization — never recorded.

        It belongs on the port because taking a ``filelock`` creates a file: a
        dry-run that locked would leave one behind.
        """


class ApplyMaterializer(Materializer):
    """The real session: perform the write, then record it."""

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self._record(describe_write_text(path, content))

    def write_private_text(self, path: Path, content: str) -> None:
        from ai_hats_core.atomic_io import atomic_write_text

        atomic_write_text(path, content, mode=0o600)
        self._record(MaterializationEntry(kind=WriteKind.WRITE_TEXT, target=path))

    def write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o700)
        self._record(describe_write_executable(path, content))

    def mkdir(self, path: Path) -> None:
        if path.is_dir():
            return
        path.mkdir(parents=True, exist_ok=True)
        self._record(describe_mkdir(path))

    def copy_tree(self, src: Path, dest: Path) -> None:
        entry = describe_copy_tree(src, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        self._record(entry)

    def symlink(self, src: Path, dest: Path) -> None:
        if dest.is_symlink() and dest.readlink() == src:
            return
        if dest.is_symlink() or dest.exists():
            raise FileExistsError("session materialization path collision")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src, target_is_directory=src.is_dir())
        self._record(describe_symlink(src, dest))

    def merge_json(self, path: Path, data: dict) -> bool:
        if not json_differs(path, data):
            return False
        entry = describe_merge_json(path, data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_json(data))
        self._record(entry)
        return True

    def remove_tree(self, path: Path) -> None:
        if not path.exists():
            return
        entry = describe_remove_tree(path)
        shutil.rmtree(path)  # safe-delete: ok caller-owned session artifact
        self._record(entry)

    @contextlib.contextmanager
    def lock(self, path: Path):
        import filelock

        path.parent.mkdir(parents=True, exist_ok=True)
        flock = filelock.FileLock(str(path), timeout=self._lock_timeout)
        try:
            with flock:
                yield
        except filelock.Timeout as exc:
            raise RuntimeError(
                f"materialization blocked >{self._lock_timeout:g}s on lock {path} — "
                f"a stuck ai-hats process likely holds it. "
                f"If safe, remove the lock file and retry."
            ) from exc


class PlanMaterializer(Materializer):
    """``--dry-run``: record the same entry, leave the filesystem untouched.

    Carries a small overlay of what it *would* have created and removed. Without
    it the record diverges from ApplyMaterializer's on ordinary sequences — four
    category handlers mkdir'ing one cache dir, or the rebuild's remove-then-mkdir.
    """

    def __init__(self) -> None:
        super().__init__()
        self._created: set[Path] = set()
        self._removed: set[Path] = set()

    def _would_exist(self, path: Path) -> bool:
        if path in self._created:
            return True
        if any(path == gone or gone in path.parents for gone in self._removed):
            return False
        return path.exists()

    def _mark_created(self, path: Path) -> None:
        self._removed.discard(path)
        self._created.update([path, *path.parents])

    def write_text(self, path: Path, content: str) -> None:
        self._mark_created(path.parent)  # apply creates parents without recording
        self._record(describe_write_text(path, content))

    def write_private_text(self, path: Path, content: str) -> None:
        self._mark_created(path)
        self._record(MaterializationEntry(kind=WriteKind.WRITE_TEXT, target=path))

    def write_executable(self, path: Path, content: str) -> None:
        self._mark_created(path.parent)
        self._record(describe_write_executable(path, content))

    def mkdir(self, path: Path) -> None:
        if self._would_exist(path):
            return
        self._mark_created(path)
        self._record(describe_mkdir(path))

    def copy_tree(self, src: Path, dest: Path) -> None:
        self._mark_created(dest)
        self._record(describe_copy_tree(src, dest))

    def symlink(self, src: Path, dest: Path) -> None:
        if self._would_exist(dest):
            raise FileExistsError("session materialization path collision")
        self._mark_created(dest)
        self._record(describe_symlink(src, dest))

    def merge_json(self, path: Path, data: dict) -> bool:
        if not json_differs(path, data):
            return False
        self._mark_created(path.parent)
        self._record(describe_merge_json(path, data))
        return True

    def remove_tree(self, path: Path) -> None:
        if not self._would_exist(path):
            return
        self._created.discard(path)
        self._removed.add(path)
        self._record(describe_remove_tree(path))

    @contextlib.contextmanager
    def lock(self, path: Path):
        yield  # nothing is written, so nothing needs serialising
