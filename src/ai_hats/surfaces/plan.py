"""The materialization plan — the one value a harness is handed (ADR-0036).

Two halves, one frozen value compared with ``==``: the composition half says
WHAT a session is made of and knows no surface; the effect half says what one
surface does with it, for one session root. Every path is absolute; every
record down the tree has a ``digest`` that folds its children's, so a plan's
digest changes whenever any byte it stands for does. Absence is ``None``,
never ``""``, ``[]`` or ``0`` (ADR-0005 §3). ``apply`` performs the effect
half, idempotently; today's composition arrives through ``plan_adapter``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath
from typing import cast

from ..fs_digest import dir_digest
from ..materialization import (
    LOCK_TIMEOUT,
    MaterializationEntry,
    WriteKind,
    json_differs,
    render_json,
)
from ..session_artifacts import RunMode, SessionPolicy

# ── digests fold down the tree ──────────────────────────────────────────────


class Digested:
    """A record whose ``digest`` folds its fields — a child's own ``digest`` where
    it has one, the bytes of a scalar otherwise — so nothing added to a plan
    can escape the hash by being forgotten."""

    @property
    def digest(self) -> str:
        return digest_of(self)


def digest_of(value: object) -> str:
    """sha256 over a canonical walk of ``value``; a ``Digested`` child contributes its digest."""
    h = hashlib.sha256()
    _feed(h, value, top=True)
    return h.hexdigest()


def _feed(h, value: object, *, top: bool = False) -> None:
    if isinstance(value, Digested) and not top:
        h.update(b"d:" + value.digest.encode() + b"\0")
    elif isinstance(value, MaterializationEntry):
        # By its own digest, like any child: the bytes of a private entry stay
        # out of every hash, as they stay out of every record.
        for name in ("kind", "target", "source", "private", "escape"):
            h.update(name.encode() + b"=")
            _feed(h, getattr(value, name))
        h.update(b"digest=")
        _feed(h, value.digest)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            h.update(f.name.encode() + b"=")
            _feed(h, getattr(value, f.name))
    elif isinstance(value, Mapping):
        for key in sorted(value):
            h.update(b"k:" + str(key).encode() + b"\0")
            _feed(h, value[key])
    elif isinstance(value, (tuple, list)):
        h.update(b"[%d]" % len(value))
        for item in value:
            _feed(h, item)
    elif isinstance(value, Enum):
        _feed(h, value.value)
    elif isinstance(value, Path):
        h.update(b"p:" + str(value).encode() + b"\0")
    elif value is None:
        h.update(b"n\0")
    elif isinstance(value, bool):
        h.update(b"b:1\0" if value else b"b:0\0")
    elif isinstance(value, int):
        h.update(b"i:" + str(value).encode() + b"\0")
    elif isinstance(value, float):
        h.update(b"f:" + repr(value).encode() + b"\0")  # repr is the shortest round-trip
    elif isinstance(value, str):
        h.update(b"s:" + value.encode() + b"\0")
    else:
        raise TypeError(f"no canonical bytes for {type(value).__name__} in a plan")


def _absolute(path: Path, what: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{what} must be absolute, got {path}")


def _content_at(path: Path, content_digest: str) -> str:
    """One identity for bytes AT a place: the same bytes elsewhere are another thing."""
    return hashlib.sha256(
        content_digest.encode() + b"@" + hashlib.sha256(str(path).encode()).hexdigest().encode()
    ).hexdigest()


# ── composition half ─────────────────────────────────────────── ADR-0036 D1


@dataclass(frozen=True)
class PromptMember(Digested):
    """One prompt's text, as declared: a heading renders ``### <heading>`` above it."""

    #: Full name (ADR-0034 D1): ``maintainer::prompt``, ``rules::rule_backlog_discipline``.
    name: str
    text: str
    heading: str | None


@dataclass(frozen=True)
class PromptBlock(Digested):
    """One block of the rendered prompt: it opens where its first member
    appeared and its members follow in order (ADR-0035 D5)."""

    #: ``None`` renders no heading; any name renders ``## <NAME>`` above the members.
    name: str | None
    members: tuple[PromptMember, ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError(f"block {self.name!r} has no members")

    @property
    def text(self) -> str:
        if self.name is None:
            return "\n\n".join(m.text for m in self.members)
        return f"## {self.name}\n" + "".join(
            f"\n### {m.heading}\n{m.text}\n" if m.heading else m.text for m in self.members
        )


@dataclass(frozen=True)
class Prompt(Digested):
    """The prompt is its blocks; the bytes are a rendering of them, one for
    every producer (ADR-0036 D5). A named block appears once; nameless prose
    may sit anywhere, as many times as the composition put it."""

    blocks: tuple[PromptBlock, ...]

    def __post_init__(self) -> None:
        names = [b.name for b in self.blocks if b.name is not None]
        if len(names) != len(set(names)):
            raise ValueError(f"a named block appears twice: {sorted(set(names))}")

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


@dataclass(frozen=True)
class Executable(Digested):
    """The script a hook runs, in the library, named by where it is."""

    path: Path
    #: sha256 of the file's bytes, streamed by whoever read it — the plan holds no bytes.
    content_digest: str

    def __post_init__(self) -> None:
        _absolute(self.path, "an executable's path")

    @property
    def digest(self) -> str:
        return _content_at(self.path, self.content_digest)


@dataclass(frozen=True)
class RuntimeHook(Digested):
    """Fired by the agent's own runtime — the one row a surface wires."""

    at: str
    matcher: str
    run: Executable


class OnError(str, Enum):
    REFUSE = "refuse"
    WARN = "warn"


@dataclass(frozen=True)
class ExternalHook(Digested):
    """Attached to a dependency outside the agent's runtime — git, the rack,
    the worktree lifecycle, the consent gate. No surface reads it; the
    dependency's own integration does, from the record (ADR-0036 D6)."""

    #: Who fires the point: ``git`` | ``rack`` | ``wt`` | ``consent_gate``.
    app: str
    #: Where under the app: the backlog for rack, the operation for consent;
    #: ``None`` where the app has no such level (git, wt).
    object: str | None
    #: The point itself: a git event, an FSM selector, a worktree point
    #: (``create``, ``teardown[merge]``, ``pre-merge``), a consent selector.
    at: str
    #: ``None`` ⇔ nothing to spawn: a consent point is enforced by the gate and
    #: the wrapper, which are effect-half entries, not a script of the role.
    run: Executable | None
    #: Set only where the author may choose (a check row); ``None`` where the
    #: owner fixes the policy — the git dispatcher, the wt lifecycle, consent.
    on_error: OnError | None
    #: Attribution for a refusal's message; outside the hook's identity.
    declared_by: str


@dataclass(frozen=True)
class Hooks(Digested):
    runtime: tuple[RuntimeHook, ...]
    external: tuple[ExternalHook, ...]


@dataclass(frozen=True)
class Skill(Digested):
    """A skill as the session mirrors it: two skills with one tree at two
    paths are two skills, so the digest folds the path in."""

    #: Full name: ``skills::hatrack``.
    name: str
    path: Path
    #: ``dir_digest`` of the tree, streamed by whoever read it.
    content_digest: str

    def __post_init__(self) -> None:
        _absolute(self.path, "a skill path")

    @property
    def digest(self) -> str:
        return _content_at(self.path, self.content_digest)


def home_of(run: Executable, skills: Sequence[Skill]) -> tuple[Skill, PurePath] | None:
    """The mirrored skill an executable lives in, and its path inside it —
    what a surface's manifest command, a gate's mirror rebase and a git sort
    key all derive; ``None`` for a script outside every composed skill."""
    for skill in skills:
        if run.path.is_relative_to(skill.path):
            return skill, PurePath(run.path.relative_to(skill.path))
    return None


@dataclass(frozen=True)
class TraceEntry(Digested):
    """How one term got into the composition — the audit's view of it.

    ``brought_by`` is the composite or override whose expansion carried the
    term: a role or trait (``maintainer``, ``trait-agent``), an override layer
    (``overrides::global``, ``overrides::project``), or the launched expression
    itself for a term the CLI added (``maintainer + sre``). ``removed_by`` is
    the same kind of name, for a term that left.
    """

    term: str
    brought_by: str
    removed_by: str | None


@dataclass(frozen=True)
class CompositionPlan(Digested):
    #: The canonical expression the session composed.
    identity: str
    prompt: Prompt
    skills: tuple[Skill, ...]
    hooks: Hooks
    trace: tuple[TraceEntry, ...]


# ── effect half ──────────────────────────────────────────────── ADR-0036 D1


@dataclass(frozen=True)
class Launch(Digested):
    """An argv, or the option set handed to an SDK — exactly one of the two."""

    args: tuple[str, ...] | None
    sdk_options: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if (self.args is None) == (self.sdk_options is None):
            raise ValueError("a launch is either an argv or an SDK option set")


@dataclass(frozen=True)
class MaterializationPlan(Digested):
    composition: CompositionPlan
    surface: str
    run_mode: RunMode
    policy: SessionPolicy
    #: The session root — an input of planning, so every target is absolute.
    root: Path
    entries: tuple[MaterializationEntry, ...]
    #: What ai-hats adds to the child's environment.
    env: Mapping[str, str]
    launch: Launch

    def __post_init__(self) -> None:
        _absolute(self.root, "the session root")


# ── planning refusals, as functions over the plan ────────────── ADR-0036 D2


class PlanRefused(ValueError):
    """The plan cannot be applied as written; nothing was touched."""


class DuplicateTargets(PlanRefused):
    def __init__(self, targets: Sequence[Path]) -> None:
        self.targets = tuple(targets)
        super().__init__("one target created twice: " + ", ".join(str(t) for t in targets))


class EscapeUndeclared(PlanRefused):
    def __init__(self, target: Path, root: Path) -> None:
        self.target = target
        super().__init__(f"{target} lies outside {root} and the entry does not declare escape")


class StalePlan(PlanRefused):
    """A source tree no longer holds the bytes the plan was made against."""

    def __init__(self, entry: MaterializationEntry) -> None:
        self.entry = entry
        super().__init__(f"{entry.source} changed since the plan digested it; plan again")


_CREATING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.COPY_TREE,
    WriteKind.SYMLINK,
    WriteKind.MERGE_JSON,
)


def validate(plan: MaterializationPlan) -> None:
    """Refuse a plan two entries create one target in, or that writes outside
    its root without saying so.

    A target counts once per creating entry whatever stands between them: a
    remove in the middle does not make the second creation a rebuild.
    """
    seen: dict[Path, int] = {}
    for entry in plan.entries:
        if entry.kind in _CREATING:
            seen[entry.target] = seen.get(entry.target, 0) + 1
    if duplicates := [target for target, n in seen.items() if n > 1]:
        raise DuplicateTargets(duplicates)
    root = Path(os.path.normpath(plan.root))
    for entry in plan.entries:
        if not entry.escape and not Path(os.path.normpath(entry.target)).is_relative_to(root):
            raise EscapeUndeclared(entry.target, plan.root)


# ── application: mechanical and idempotent ───────────────────── ADR-0036 D3


def apply(plan: MaterializationPlan) -> None:
    """Perform the entries in order, writing only where the disk differs.

    A tree is synced file by file; a file inside it that a later entry writes
    is left to that entry, so the two never fight. A source tree whose digest
    no longer matches the plan is refused rather than copied.
    """
    validate(plan)
    shadowed = {e.target for e in plan.entries if e.kind in _SHADOWING}
    with _lock(plan.root):
        for entry in plan.entries:
            if entry.kind is WriteKind.COPY_TREE and _stale(entry):
                raise StalePlan(entry)
        for entry in plan.entries:
            _PERFORM[entry.kind](entry, shadowed)


def _stale(entry: MaterializationEntry) -> bool:
    return (
        entry.tree_digest is not None and dir_digest(cast(Path, entry.source)) != entry.tree_digest
    )


_SHADOWING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.MERGE_JSON,
    WriteKind.SYMLINK,
)


@contextlib.contextmanager
def _lock(root: Path):
    """Serialise on a lock beside the root — inside it, a remove_tree would take it away."""
    import filelock

    if not root.parent.is_dir():
        root.parent.mkdir(parents=True)
    lock = filelock.FileLock(str(root.parent / f"{root.name}.lock"), timeout=LOCK_TIMEOUT)
    try:
        with lock:
            yield
    except filelock.Timeout as exc:
        raise RuntimeError(
            f"materialization blocked >{LOCK_TIMEOUT:g}s on lock {lock.lock_file} — "
            "a stuck ai-hats process likely holds it. If safe, remove the lock file and retry."
        ) from exc


def _ensure_parent(path: Path) -> None:
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)


def _same_bytes(path: Path, payload: bytes) -> bool:
    return path.is_file() and not path.is_symlink() and path.read_bytes() == payload


def _make_way_for_a_file(path: Path) -> None:
    """Whatever stands where a file belongs — a link, a directory — goes first.

    Writing through a symlink would reach outside the plan; a directory would
    swallow the copy and leave the file missing.
    """
    if path.is_symlink():
        path.unlink()  # safe-delete: ok a link standing where the plan puts a file
    elif path.is_dir():
        shutil.rmtree(path)  # safe-delete: ok a directory standing where the plan puts a file
    else:
        _ensure_parent(path)


def _write_bytes(path: Path, content: str) -> None:
    _make_way_for_a_file(path)
    path.write_text(content, encoding="utf-8")


def _write_text(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    content, payload = cast(str, entry.content), cast(bytes, entry.bytes)
    if entry.private:
        from ai_hats_core.atomic_io import atomic_write_text

        if _same_bytes(entry.target, payload) and entry.target.stat().st_mode & 0o777 == 0o600:
            return
        _make_way_for_a_file(entry.target)
        atomic_write_text(entry.target, content, mode=0o600)
        return
    if _same_bytes(entry.target, payload):
        return
    _write_bytes(entry.target, content)


def _write_executable(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    content, payload = cast(str, entry.content), cast(bytes, entry.bytes)
    if not _same_bytes(entry.target, payload):
        _write_bytes(entry.target, content)
    if entry.target.stat().st_mode & 0o777 != 0o700:
        entry.target.chmod(0o700)


def _copy_tree(entry: MaterializationEntry, shadowed: set[Path]) -> None:
    source, target = cast(Path, entry.source), entry.target
    if target.is_symlink():
        target.unlink()  # safe-delete: ok a link standing where the plan puts a tree
    wanted: set[Path] = set()
    for file in sorted(p for p in source.rglob("*") if p.is_file()):
        dest = target / file.relative_to(source)
        wanted.add(dest)
        if dest in shadowed:
            continue
        if (
            dest.is_file()
            and not dest.is_symlink()
            and dest.read_bytes() == file.read_bytes()
            and dest.stat().st_mode & 0o777 == file.stat().st_mode & 0o777
        ):
            continue
        _make_way_for_a_file(dest)
        shutil.copy2(file, dest)
    if target.is_dir():
        for stray in sorted(p for p in target.rglob("*") if p.is_file() or p.is_symlink()):
            if stray not in wanted and stray not in shadowed:
                stray.unlink()  # safe-delete: ok stray file in a session mirror the plan owns


def _symlink(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    source, target = cast(Path, entry.source), entry.target
    if target.is_symlink():
        if target.readlink() == source:
            return
        target.unlink()  # safe-delete: ok a session symlink being repointed
    elif target.exists():
        raise FileExistsError(f"session materialization path collision at {target}")
    _ensure_parent(target)
    target.symlink_to(source, target_is_directory=source.is_dir())


def _merge_json(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    data = dict(cast(Mapping[str, object], entry.data))
    if not entry.target.is_symlink() and not json_differs(entry.target, data):
        return
    _write_bytes(entry.target, render_json(data))


def _remove_tree(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    """Ensure absent — a link or a file at the target is removed as itself, not followed."""
    if entry.target.is_symlink() or entry.target.is_file():
        entry.target.unlink()  # safe-delete: ok caller-owned session artifact
    elif entry.target.is_dir():
        shutil.rmtree(entry.target)  # safe-delete: ok caller-owned session artifact


def _mkdir(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    if not entry.target.is_dir():
        entry.target.mkdir(parents=True)


_PERFORM = {
    WriteKind.WRITE_TEXT: _write_text,
    WriteKind.WRITE_EXECUTABLE: _write_executable,
    WriteKind.COPY_TREE: _copy_tree,
    WriteKind.SYMLINK: _symlink,
    WriteKind.MERGE_JSON: _merge_json,
    WriteKind.REMOVE_TREE: _remove_tree,
    WriteKind.MKDIR: _mkdir,
}


# ── the record: the composition half as a JSON document ──────── ADR-0036 D5, D6


def composition_record(plan: CompositionPlan) -> dict:
    """The composition half as the session record and ``--dry-run-json`` carry it.

    Bytes stay out — the prompt is its blocks and members here, an executable
    its path and digests.
    """

    def executable(e: Executable) -> dict:
        return {"path": str(e.path), "content_digest": e.content_digest, "digest": e.digest}

    hooks = plan.hooks
    return {
        "identity": plan.identity,
        "digest": plan.digest,
        "prompt": {
            "blocks": [
                {
                    "name": b.name,
                    "members": [{"name": m.name, "heading": m.heading} for m in b.members],
                }
                for b in plan.prompt.blocks
            ]
        },
        "skills": [
            {
                "name": s.name,
                "path": str(s.path),
                "content_digest": s.content_digest,
                "digest": s.digest,
            }
            for s in plan.skills
        ],
        "hooks": {
            "runtime": [
                {"at": h.at, "matcher": h.matcher, "run": executable(h.run)} for h in hooks.runtime
            ],
            "external": [
                {
                    "app": h.app,
                    "object": h.object,
                    "at": h.at,
                    "run": None if h.run is None else executable(h.run),
                    "on_error": None if h.on_error is None else h.on_error.value,
                    "declared_by": h.declared_by,
                }
                for h in hooks.external
            ],
        },
        "trace": [
            {"term": t.term, "brought_by": t.brought_by, "removed_by": t.removed_by}
            for t in plan.trace
        ],
    }
