"""The entry — one write of a session, carrying what it writes (ADR-0036 D1).

A plan is a tuple of these; ``surfaces.plan.apply`` performs them. Nothing here
reads or writes the disk: an entry is an instruction, and its digest and size
are derived from the payload it holds, never stored beside it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType

# A rebuild is a sub-second fs op, so a timeout means a stuck/dead holder.
LOCK_TIMEOUT = 30.0


class WriteKind(str, Enum):
    WRITE_TEXT = "write_text"
    WRITE_EXECUTABLE = "write_executable"
    COPY_TREE = "copy_tree"
    #: One file whose bytes live outside the plan — a credential in the user's
    #: home — read at application, like a tree's.
    COPY_FILE = "copy_file"
    SYMLINK = "symlink"
    #: ``data`` is what ai-hats adds to the document; application merges it
    #: into whatever the file holds and leaves the rest alone.
    MERGE_JSON = "merge_json"
    REMOVE_TREE = "remove_tree"
    MKDIR = "mkdir"


#: What each kind writes from — the one field it must carry, and the only one.
_PAYLOAD_OF = {
    WriteKind.WRITE_TEXT: "content",
    WriteKind.WRITE_EXECUTABLE: "content",
    WriteKind.COPY_TREE: "source",
    WriteKind.COPY_FILE: "source",
    WriteKind.SYMLINK: "source",
    WriteKind.MERGE_JSON: "data",
    WriteKind.REMOVE_TREE: None,
    WriteKind.MKDIR: None,
}

_PRIVATE_KINDS = (WriteKind.WRITE_TEXT, WriteKind.COPY_FILE)


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
        if self.private and self.kind not in _PRIVATE_KINDS:
            raise ValueError(f"{self.kind.value} entry cannot be private")
        if self.data is not None:  # the caller's dict stays the caller's, nested too
            object.__setattr__(self, "data", MappingProxyType(copy.deepcopy(dict(self.data))))

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


# --- describe: the pure constructors every planner spells an entry with ---


def describe_write_text(path: Path, content: str) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.WRITE_TEXT, target=path, content=content)


def describe_private_text(path: Path, content: str) -> MaterializationEntry:
    return MaterializationEntry(
        kind=WriteKind.WRITE_TEXT, target=path, content=content, private=True
    )


def describe_write_executable(path: Path, content: str) -> MaterializationEntry:
    return replace(describe_write_text(path, content), kind=WriteKind.WRITE_EXECUTABLE)


def describe_copy_file(src: Path, dest: Path, *, private: bool = False) -> MaterializationEntry:
    return MaterializationEntry(kind=WriteKind.COPY_FILE, target=dest, source=src, private=private)


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
