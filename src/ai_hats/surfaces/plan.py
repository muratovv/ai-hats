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
from .hook_channel import HookEvent
from .managed_tags import CLAUDE_TAG_KEY as MANAGED_TAG_KEY

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

    at: HookEvent
    #: Written in the channel's matcher vocabulary (ADR-0020), which every
    #: surface maps its native tool names onto.
    matcher: str
    run: Executable

    def __post_init__(self) -> None:
        if not isinstance(self.at, HookEvent):
            raise ValueError(f"a runtime hook binds to a HookEvent, got {self.at!r}")


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
    #: ``SKILL.md`` as the agent reads it — placeholders and the FSM edge table
    #: expanded for the layout, by whoever read the tree; ``None`` where it ships none.
    document: str | None = None
    #: Subdirectories whose contents the agent calls by name (``scripts``,
    #: ``bin``): only those the tree has, so a planner puts nothing absent on PATH.
    on_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _absolute(self.path, "a skill path")

    @property
    def digest(self) -> str:
        return _content_at(self.path, self.content_digest)


_SKILLS_NS = "skills::"


def mirror_name(skill: Skill) -> str:
    """The directory a surface mirrors the skill under: its name below ``skills::``."""
    if not skill.name.startswith(_SKILLS_NS):
        raise ValueError(f"not a skill name: {skill.name!r}")
    return skill.name[len(_SKILLS_NS) :]


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


# ── what planning is told about the machine ──────────────────── ADR-0036 D2


@dataclass(frozen=True)
class Host(Digested):
    """Facts of the machine a session runs on, handed to planning as a value:
    probed once by the stage before it, never read by a planner."""

    #: The interpreter hooks, wrappers and the form server run under.
    python: Path
    #: The ``PATH`` the child inherits, with no consent-wrapper directory in it.
    path: str
    #: Where each command the consent gate can wrap resolves; a command that is
    #: not on the path has no key.
    commands: Mapping[str, Path]
    #: The person's configuration home as the surface projects it into a session
    #: (``Surface.probe_home``); ``None`` for a surface that projects none.
    home: Digested | None = None


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
    #: What the surface hands the agent: the composition's blocks, then its own
    #: (a skill index, a harness section); the context entry carries ``prompt.text``.
    prompt: Prompt
    surface: str
    run_mode: RunMode
    policy: SessionPolicy
    #: The session root — an input of planning, so every target is absolute.
    root: Path
    entries: tuple[MaterializationEntry, ...]
    #: What ai-hats adds to the child's environment.
    env: Mapping[str, str]
    launch: Launch
    #: The target of the entry that carries the agent's context; ``None`` where
    #: the surface hands the prompt inline (an argv token, a config document).
    context: Path | None = None

    def __post_init__(self) -> None:
        _absolute(self.root, "the session root")
        if self.context is not None:
            _absolute(self.context, "the context path")
        own = self.composition.prompt.blocks
        if self.prompt.blocks[: len(own)] != own:
            raise ValueError("the surface prompt must open with the composition's blocks")


# ── the launch pair ──────────────────────────────────────────── ADR-0036 D4


@dataclass(frozen=True)
class LaunchFlags:
    """What changes how a plan is invoked and never the plan itself."""

    session_id: str
    #: Where the session's record lives — what a gate reads the plan back from.
    session_dir: Path
    trace_path: str
    root_pid: str
    #: The harness's own session id; ``None`` where the harness mints it.
    provider_session_id: str | None
    #: The operator's additions to the argv.
    extra_args: tuple[str, ...] = ()
    #: Where the child runs; ``None`` is the project root.
    work_dir: Path | None = None
    model: str | None = None
    #: The sub-agent's first turn, assembled by the caller that owns the card
    #: it comes from — so a launch never resolves a tracker id. ``None`` in HITL.
    brief: str | None = None
    #: Take resources for real (a bound port); a report leaves them unclaimed.
    claim: bool = True


@dataclass(frozen=True)
class Launched:
    """What the harness is handed: an argv or an SDK option document, exactly
    one of the two, with the environment ai-hats adds and the bytes the agent reads."""

    args: tuple[str, ...] | None
    sdk_options: Mapping[str, object] | None
    env: Mapping[str, str]
    prompt: str

    def __post_init__(self) -> None:
        if (self.args is None) == (self.sdk_options is None):
            raise ValueError("a launch is either an argv or an SDK option document")


def context_entry(plan: MaterializationPlan) -> MaterializationEntry | None:
    """The entry that carries the agent's context — the one the plan names;
    ``None`` where the surface hands the prompt inline."""
    if plan.context is None:
        return None
    wanted = _normal(plan.context)
    for entry in plan.entries:
        if entry.kind is WriteKind.WRITE_TEXT and _normal(entry.target) == wanted:
            return entry
    raise ContextUnwritten(plan.context)


def context_text(plan: MaterializationPlan) -> str:
    """The bytes the agent reads as its context: the context entry's, or the
    surface prompt where the plan hands it inline; nothing under a policy that
    withholds the context, whatever the prompt holds."""
    if not plan.policy.context:
        return ""
    entry = context_entry(plan)
    return cast(str, entry.content) if entry is not None else plan.prompt.text


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


class ContextUnwritten(PlanRefused):
    def __init__(self, context: Path) -> None:
        self.context = context
        super().__init__(f"the plan names {context} as its context and no entry writes it")


class StalePlan(PlanRefused):
    """A source no longer holds what the plan was made against — a tree whose
    digest moved, a file that is gone."""

    def __init__(self, entry: MaterializationEntry) -> None:
        self.entry = entry
        super().__init__(f"{entry.source} is not what the plan was made against; plan again")


class UnmergeableTarget(PlanRefused):
    """A JSON merge target that holds something other than a JSON object —
    a person's file, refused rather than overwritten."""

    def __init__(self, entry: MaterializationEntry, why: str) -> None:
        self.entry = entry
        super().__init__(f"{entry.target} cannot be merged into: {why}")


_CREATING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.COPY_TREE,
    WriteKind.COPY_FILE,
    WriteKind.SYMLINK,
    WriteKind.MERGE_JSON,
)


def validate(plan: MaterializationPlan) -> None:
    """Refuse a plan two entries create one target in, that writes outside
    its root without saying so, or that names a context no entry writes.

    A target counts once per creating entry whatever stands between them: a
    remove in the middle does not make the second creation a rebuild.
    """
    seen: dict[Path, int] = {}
    for entry in plan.entries:
        if entry.kind in _CREATING:
            target = _normal(entry.target)
            seen[target] = seen.get(target, 0) + 1
    if duplicates := [target for target, n in seen.items() if n > 1]:
        raise DuplicateTargets(duplicates)
    root = _normal(plan.root)
    for entry in plan.entries:
        if not entry.escape and not _normal(entry.target).is_relative_to(root):
            raise EscapeUndeclared(entry.target, plan.root)
    context_entry(plan)


def _normal(path: Path) -> Path:
    """Lexically normalised — ``a/../b`` is ``b`` — without touching the disk."""
    return Path(os.path.normpath(path))


# ── application: mechanical and idempotent ───────────────────── ADR-0036 D3


class Outcome(str, Enum):
    """What one application did to one entry."""

    WRITTEN = "written"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    ABSENT = "absent"


@dataclass(frozen=True)
class AppliedEntry:
    entry: MaterializationEntry
    outcome: Outcome
    #: Files the tree holds after the sync — a fact of application; ``None`` elsewhere.
    files: int | None


@dataclass(frozen=True)
class Applied:
    """The outcome of one application, one row per entry in the plan's order —
    what the session record and a ``--materialize`` note say happened."""

    entries: tuple[AppliedEntry, ...]

    @property
    def changed(self) -> bool:
        return any(a.outcome in (Outcome.WRITTEN, Outcome.REMOVED) for a in self.entries)


def apply(plan: MaterializationPlan) -> Applied:
    """Perform the entries in order, writing only where the disk differs.

    A tree is synced file by file; a file inside it that a later entry writes
    is left to that entry, so the two never fight. A source tree whose digest
    no longer matches the plan is refused rather than copied.
    """
    validate(plan)
    with _lock(plan.root):
        for entry in plan.entries:
            if _stale(entry):
                raise StalePlan(entry)
            if entry.kind is WriteKind.MERGE_JSON:
                _current_document(entry)
        done: list[AppliedEntry] = []
        for i, entry in enumerate(plan.entries):
            later = {e.target for e in plan.entries[i + 1 :] if e.kind in _SHADOWING}
            done.append(_PERFORM[entry.kind](entry, later))
    return Applied(tuple(done))


def _did(entry: MaterializationEntry, written: bool, files: int | None = None) -> AppliedEntry:
    return AppliedEntry(entry, Outcome.WRITTEN if written else Outcome.UNCHANGED, files)


def _stale(entry: MaterializationEntry) -> bool:
    if entry.kind is WriteKind.COPY_FILE:
        return not cast(Path, entry.source).is_file()
    return (
        entry.tree_digest is not None and dir_digest(cast(Path, entry.source)) != entry.tree_digest
    )


def _current_document(entry: MaterializationEntry) -> dict:
    """What a merge target holds now: an empty document where there is no file."""
    import json

    if entry.target.is_symlink() or not entry.target.exists():
        return {}
    if not entry.target.is_file():
        raise UnmergeableTarget(entry, "not a file")
    try:
        document = json.loads(entry.target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UnmergeableTarget(entry, f"not JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise UnmergeableTarget(entry, "a JSON value that is not an object")
    return document


def _merge_document(current: object, patch: object) -> object:
    """``patch`` over ``current``: objects merge key by key; a list merges by
    managed tag — the entries ai-hats tagged are replaced as a set, a person's
    are kept — and a list without tags, like a scalar, is replaced whole."""
    if isinstance(current, Mapping) and isinstance(patch, Mapping):
        merged = dict(current)
        for key, value in patch.items():
            merged[key] = _merge_document(current.get(key), value) if key in current else value
        return merged
    if isinstance(current, list) and isinstance(patch, list) and any(map(_tag_of, patch)):
        theirs = [item for item in current if _tag_of(item) is None]
        return [*theirs, *patch]
    return patch


def _tag_of(item: object) -> str | None:
    tag = item.get(MANAGED_TAG_KEY) if isinstance(item, Mapping) else None
    return tag if isinstance(tag, str) else None


_SHADOWING = (
    WriteKind.WRITE_TEXT,
    WriteKind.WRITE_EXECUTABLE,
    WriteKind.COPY_FILE,
    WriteKind.MERGE_JSON,
    WriteKind.SYMLINK,
)


@contextlib.contextmanager
def _lock(root: Path):
    """Serialise on a lock beside the root — inside it, a remove_tree would take it away."""
    import filelock

    # exist_ok: two processes applying one root both find it missing at once.
    root.parent.mkdir(parents=True, exist_ok=True)
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


def _put_bytes(path: Path, payload: bytes, *, private: bool) -> bool:
    """Write ``payload`` at ``path`` unless it is already there with the right mode."""
    if private:
        from ai_hats_core.atomic_io import atomic_write_bytes

        if _same_bytes(path, payload) and path.stat().st_mode & 0o777 == 0o600:
            return False
        _make_way_for_a_file(path)
        atomic_write_bytes(path, payload, mode=0o600)
        return True
    if _same_bytes(path, payload):
        return False
    _make_way_for_a_file(path)
    path.write_bytes(payload)
    return True


def _write_text(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    return _did(entry, _put_bytes(entry.target, cast(bytes, entry.bytes), private=entry.private))


def _copy_file(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    payload = cast(Path, entry.source).read_bytes()
    return _did(entry, _put_bytes(entry.target, payload, private=entry.private))


def _write_executable(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    content, payload = cast(str, entry.content), cast(bytes, entry.bytes)
    written = False
    if not _same_bytes(entry.target, payload):
        _write_bytes(entry.target, content)
        written = True
    if entry.target.stat().st_mode & 0o777 != 0o700:
        entry.target.chmod(0o700)
        written = True
    return _did(entry, written)


def _copy_tree(entry: MaterializationEntry, shadowed: set[Path]) -> AppliedEntry:
    """Sync ``source`` onto ``target`` the way ``copytree`` would have written
    it: every directory, links followed, and nothing else left behind."""
    source, target = cast(Path, entry.source), entry.target
    written = False
    if target.is_symlink():
        target.unlink()  # safe-delete: ok a link standing where the plan puts a tree
        written = True
    wanted_files: set[Path] = set()
    wanted_dirs: set[Path] = set()
    for dirpath, _dirnames, filenames in os.walk(source, followlinks=True):
        here = Path(dirpath)
        dest_dir = target / here.relative_to(source)
        wanted_dirs.add(dest_dir)
        written |= _make_way_for_a_dir(dest_dir)
        for name in sorted(filenames):
            file, dest = here / name, dest_dir / name
            wanted_files.add(dest)
            if dest in shadowed or _same_file(dest, file):
                continue
            _make_way_for_a_file(dest)
            shutil.copy2(file, dest)
            written = True
    for stray in sorted(p for p in target.rglob("*") if p.is_file() or p.is_symlink()):
        if stray not in wanted_files and stray not in shadowed:
            stray.unlink()  # safe-delete: ok stray file in a session mirror the plan owns
            written = True
    for stray_dir in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        if stray_dir not in wanted_dirs and not any(stray_dir.iterdir()):
            stray_dir.rmdir()  # safe-delete: ok empty-dir in a session mirror the plan owns
            written = True
    return _did(entry, written, files=len(wanted_files))


def _same_file(dest: Path, file: Path) -> bool:
    return (
        dest.is_file()
        and not dest.is_symlink()
        and dest.read_bytes() == file.read_bytes()
        and dest.stat().st_mode & 0o777 == file.stat().st_mode & 0o777
    )


def _make_way_for_a_dir(path: Path) -> bool:
    """Whether anything had to change for a directory to stand at ``path``."""
    if path.is_symlink() or path.is_file():
        path.unlink()  # safe-delete: ok a link or a file standing where the plan puts a directory
    if path.is_dir():
        return False
    path.mkdir(parents=True)
    return True


def _symlink(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    source, target = cast(Path, entry.source), entry.target
    if target.is_symlink():
        if target.readlink() == source:
            return _did(entry, False)
        target.unlink()  # safe-delete: ok a session symlink being repointed
    elif target.exists():
        raise FileExistsError(f"session materialization path collision at {target}")
    _ensure_parent(target)
    target.symlink_to(source, target_is_directory=source.is_dir())
    return _did(entry, True)


def _merge_json(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    merged = cast(dict, _merge_document(_current_document(entry), dict(cast(Mapping, entry.data))))
    if not entry.target.is_symlink() and not json_differs(entry.target, merged):
        return _did(entry, False)
    _write_bytes(entry.target, render_json(merged))
    return _did(entry, True)


def _remove_tree(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    """Ensure absent — a link or a file at the target is removed as itself, not followed."""
    if entry.target.is_symlink() or entry.target.is_file():
        entry.target.unlink()  # safe-delete: ok caller-owned session artifact
    elif entry.target.is_dir():
        shutil.rmtree(entry.target)  # safe-delete: ok caller-owned session artifact
    else:
        return AppliedEntry(entry, Outcome.ABSENT, None)
    return AppliedEntry(entry, Outcome.REMOVED, None)


def _mkdir(entry: MaterializationEntry, _shadowed: set[Path]) -> AppliedEntry:
    if entry.target.is_dir():
        return _did(entry, False)
    entry.target.mkdir(parents=True)
    return _did(entry, True)


_PERFORM = {
    WriteKind.WRITE_TEXT: _write_text,
    WriteKind.WRITE_EXECUTABLE: _write_executable,
    WriteKind.COPY_TREE: _copy_tree,
    WriteKind.COPY_FILE: _copy_file,
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
                {"at": h.at.value, "matcher": h.matcher, "run": executable(h.run)}
                for h in hooks.runtime
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


def checks_record(plan: MaterializationPlan) -> list[dict]:
    """The check bindings as the record names them: where each runs from in
    the session's mirror, read off the plan's own tree entries — nothing is
    resolved on disk. A script outside every composed skill has no mirror and
    says so; the adapter reported it when it read the library."""
    mirrored = {
        e.source: e.target
        for e in plan.entries
        if e.kind is WriteKind.COPY_TREE and e.source is not None
    }
    rows = []
    for hook in plan.composition.hooks.external:
        if hook.on_error is None or hook.run is None:
            continue
        home = home_of(hook.run, plan.composition.skills)
        runs_from = None
        if home is not None:
            skill, inside = home
            target = mirrored.get(skill.path)
            runs_from = None if target is None else str(target / inside)
        rows.append(
            {
                "skill": None if home is None else mirror_name(home[0]),
                "script": str(hook.run.path) if home is None else str(home[1]),
                "app": hook.app,
                "object": hook.object,
                "at": hook.at,
                "on_error": hook.on_error.value,
                "declared_by": hook.declared_by,
                "runs_from": runs_from,
                "planned": runs_from is not None,
            }
        )
    return rows
