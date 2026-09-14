"""Materialization port — the one seam every session write goes through.

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
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType

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


#: What each kind writes from — the one field it must carry, and the only one.
_PAYLOAD_OF = {
    WriteKind.WRITE_TEXT: "content",
    WriteKind.WRITE_EXECUTABLE: "content",
    WriteKind.COPY_TREE: "source",
    WriteKind.SYMLINK: "source",
    WriteKind.MERGE_JSON: "data",
    WriteKind.REMOVE_TREE: None,
    WriteKind.MKDIR: None,
}


@dataclass(frozen=True)
class MaterializationEntry:
    """One write, carrying what it writes — so applying it needs nothing else.

    ``digest`` and ``size`` are derived from the payload, never stored: an entry
    cannot claim bytes it does not hold. A tree's bytes stay on disk, so its
    digest arrives as ``tree_digest`` — an input, streamed by whoever read the
    tree — and its size is a fact of application, ``None`` here.
    """

    kind: WriteKind
    target: Path
    #: Out of ``repr``: a prompt is tens of KB, a private file is a credential.
    content: str | None = field(default=None, repr=False)
    data: Mapping[str, object] | None = None
    source: Path | None = None
    #: Owner-only mode, and no digest in any record of this entry.
    private: bool = False
    #: The target lies outside the plan's root, and the planner meant it.
    escape: bool = False
    tree_digest: str | None = None

    def __post_init__(self) -> None:
        payload = _PAYLOAD_OF[self.kind]
        carried = {
            name for name in ("content", "data", "source") if getattr(self, name) is not None
        }
        if carried != ({payload} if payload else set()):
            raise ValueError(f"{self.kind.value} entry carries {sorted(carried)}, needs {payload}")
        if self.tree_digest is not None and self.kind is not WriteKind.COPY_TREE:
            raise ValueError(f"{self.kind.value} entry cannot carry a tree digest")
        if self.private and self.kind is not WriteKind.WRITE_TEXT:
            raise ValueError(f"{self.kind.value} entry cannot be private")
        if self.data is not None:  # the caller's dict stays the caller's
            object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    @property
    def bytes(self) -> bytes | None:
        """The bytes a text or json entry writes; ``None`` for every other kind."""
        if self.content is not None:
            return self.content.encode()
        if self.data is not None:
            return render_json(dict(self.data)).encode()
        return None

    @property
    def size(self) -> int | None:
        payload = self.bytes
        return None if payload is None else len(payload)

    @property
    def digest(self) -> str | None:
        if self.private:
            return None
        if self.kind is WriteKind.COPY_TREE:
            return self.tree_digest
        payload = self.bytes
        return None if payload is None else hashlib.sha256(payload).hexdigest()


_CREATING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.COPY_TREE,
    WriteKind.SYMLINK,
    WriteKind.MERGE_JSON,
)


@dataclass
class MaterializationRecord:
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
    return MaterializationEntry(kind=WriteKind.WRITE_TEXT, target=path, content=content)


def describe_private_text(path: Path, content: str) -> MaterializationEntry:
    return MaterializationEntry(
        kind=WriteKind.WRITE_TEXT, target=path, content=content, private=True
    )


def describe_write_executable(path: Path, content: str) -> MaterializationEntry:
    return replace(describe_write_text(path, content), kind=WriteKind.WRITE_EXECUTABLE)


def describe_copy_tree(src: Path, dest: Path) -> MaterializationEntry:
    return MaterializationEntry(
        kind=WriteKind.COPY_TREE,
        target=dest,
        source=src,
        tree_digest=dir_digest(src) if src.is_dir() else None,
    )


def describe_symlink(src: Path, dest: Path) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.SYMLINK, target=dest, source=src)


def describe_mkdir(path: Path) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.MKDIR, target=path)


def describe_remove_tree(path: Path) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.REMOVE_TREE, target=path)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def describe_merge_json(path: Path, data: dict) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.MERGE_JSON, target=path, data=data)


def json_differs(path: Path, data: dict) -> bool:
    current = path.read_text() if path.is_file() else None
    return current != render_json(data)


# --- the port ---


class Materializer(abc.ABC):
    """Every session write goes through here. Read ``plan`` after the build."""

    def __init__(self, *, lock_timeout: float = LOCK_TIMEOUT) -> None:
        self.record = MaterializationRecord()
        self._lock_timeout = lock_timeout

    def _record(self, entry: MaterializationEntry) -> None:
        self.record.entries.append(entry)

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
    def executable_at(self, path: Path) -> bool:
        """Whether an executable file sits at ``path`` once this build has run.

        Hook writers ask it about the session mirror another handler copied; a
        dry-run has to answer the way the real build would, from its record.
        """

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
        self._record(describe_private_text(path, content))

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

    def executable_at(self, path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK)

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
        self._record(describe_private_text(path, content))

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

    def executable_at(self, path: Path) -> bool:
        for entry in reversed(self.record.entries):
            covers = path == entry.target or entry.target in path.parents
            if not covers:
                continue
            if entry.kind is WriteKind.COPY_TREE and entry.source is not None:
                source = entry.source / path.relative_to(entry.target)
                return source.is_file() and os.access(source, os.X_OK)
            if entry.kind is WriteKind.WRITE_EXECUTABLE:
                return path == entry.target
            if entry.kind is WriteKind.REMOVE_TREE:
                return False
        return path.is_file() and os.access(path, os.X_OK)

    @contextlib.contextmanager
    def lock(self, path: Path):
        yield  # nothing is written, so nothing needs serialising
