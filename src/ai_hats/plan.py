"""The materialization plan — the one value a harness is handed (ADR-0036).

Two halves, one frozen value compared with ``==``: the composition half says
WHAT a session is made of and knows no surface; the effect half says what one
surface does with it, for one session root. Absence is ``None``, never ``""``,
``[]`` or ``0`` (ADR-0005 §3). ``adapt`` is the tail of today's path — the one
stage that reads the library; ``apply`` performs the effect half, idempotently.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ai_hats_core import CompositionResult

from .diagnostics import Level
from .fs_digest import dir_digest
from .materialization import (
    LOCK_TIMEOUT,
    MaterializationEntry,
    WriteKind,
    json_differs,
    render_json,
)
from .session_artifacts import RunMode, SessionPolicy

if TYPE_CHECKING:
    from .config.overlay import OverlayConfig
    from .resolver import LibraryResolver


# ── composition half ─────────────────────────────────────────── ADR-0036 D1


@dataclass(frozen=True)
class PromptMember:
    #: The block the text landed in — declared by the prompt, an open set.
    block: str
    #: Full name (ADR-0034 D1): ``maintainer::prompt``, ``rules::rule_backlog_discipline``.
    name: str


@dataclass(frozen=True)
class Prompt:
    text: str
    members: tuple[PromptMember, ...]


@dataclass(frozen=True)
class GitHook:
    at: str
    #: The payload's path from its layer root — the same string under every DSL.
    run: str


@dataclass(frozen=True)
class RuntimeHook:
    at: str
    matcher: str
    run: str


class OnError(str, Enum):
    REFUSE = "refuse"
    WARN = "warn"


@dataclass(frozen=True)
class WorkflowHook:
    app: str
    #: Where under the app the row sat (``tasks`` for rack); ``None`` when the
    #: row sits directly under the app key.
    object: str | None
    #: ONE point — a row naming N points becomes N hooks.
    at: str
    run: str
    on_error: OnError


@dataclass(frozen=True)
class WorktreeHook:
    at: str
    #: Teardown events a ``wt_out`` hook fires on; ``None`` for ``wt_in``.
    on: tuple[str, ...] | None
    run: str


@dataclass(frozen=True)
class Hooks:
    git: tuple[GitHook, ...]
    runtime: tuple[RuntimeHook, ...]
    workflow: tuple[WorkflowHook, ...]
    worktree: tuple[WorktreeHook, ...]


@dataclass(frozen=True)
class Consent:
    operation: str
    #: The selector as declared; ``source`` / ``target`` are its parsed ends,
    #: spelled ``from`` / ``to`` in the record a stdlib-only guard reads.
    at: str
    source: str | None
    target: str | None
    declared_by: str
    #: Absent ⇔ the point is armed. Never filled on the ``-r`` path: the
    #: composer drops a disarmed point before the adapter sees it.
    disarmed_by: str | None


@dataclass(frozen=True)
class TraceEntry:
    term: str
    #: The composite or override that brought the term.
    brought_by: str
    removed_by: str | None


@dataclass(frozen=True)
class Diagnostic:
    level: Level
    message: str


@dataclass(frozen=True)
class CompositionPlan:
    #: The canonical expression the session composed.
    identity: str
    prompt: Prompt
    skills: tuple[str, ...]
    hooks: Hooks
    consent: tuple[Consent, ...]
    trace: tuple[TraceEntry, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class SkillSource:
    path: Path
    digest: str


@dataclass(frozen=True)
class Sources:
    """Where the bytes behind the plan's names live — beside the plan, outside ``==``.

    Two DSLs name one skill from two roots, so a path cannot sit in the
    comparable half; the digest can, and does, as the entry's ``tree_digest``.
    """

    skills: Mapping[str, SkillSource]


# ── effect half ──────────────────────────────────────────────── ADR-0036 D1


@dataclass(frozen=True)
class Launch:
    """An argv, or the option set handed to an SDK — exactly one of the two."""

    args: tuple[str, ...] | None
    sdk_options: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if (self.args is None) == (self.sdk_options is None):
            raise ValueError("a launch is either an argv or an SDK option set")


@dataclass(frozen=True)
class MaterializationPlan:
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


# ── the adapter: CompositionResult → composition half ────────── ADR-0036 D7

_OVERRIDE_NS = "overrides"
_RUNTIME_LAYER = "runtime"


class AdaptError(RuntimeError):
    """The composition result broke an invariant the adapter relies on."""


def adapt(
    result: CompositionResult,
    *,
    identity: str,
    resolver: LibraryResolver,
    overlays: Sequence[tuple[OverlayConfig, str]] = (),
) -> tuple[CompositionPlan, Sources]:
    """Today's composition as the plan's composition half, plus where its bytes live.

    ``overlays`` are the layers the composer applied, each with its label
    (``global`` / ``project`` / ``runtime``); they and the trait configs are what
    attribute each term to the composite or override that brought it.
    """
    from .surfaces.system_prompt import compose_sections

    diagnostics: list[Diagnostic] = [
        Diagnostic(Level.WARN, err.message) for err in result.errors if not err.lossy
    ]
    return (
        CompositionPlan(
            identity=identity,
            prompt=Prompt(
                text=compose_sections(result),
                members=_prompt_members(result, overlays),
            ),
            skills=tuple(_skill_name(s.name) for s in result.skills),
            hooks=_hooks(result, diagnostics),
            consent=_consent(result),
            trace=_trace(result, identity, resolver, overlays),
            diagnostics=tuple(diagnostics),
        ),
        Sources(
            skills={
                _skill_name(s.name): SkillSource(
                    path=s.source_path, digest=dir_digest(s.source_path)
                )
                for s in result.skills
            }
        ),
    )


def _skill_name(name: str) -> str:
    return f"skills::{name}"


def _rule_name(name: str) -> str:
    return f"rules::{name}"


def _payload(skill_name: str, script: str) -> str:
    from .libraries.models import resolve_namespace

    return f"skills/{resolve_namespace(skill_name)}/{script}"


def _override_name(label: str, identity: str) -> str:
    return identity if label == _RUNTIME_LAYER else f"{_OVERRIDE_NS}::{label}"


def _prompt_members(
    result: CompositionResult, overlays: Sequence[tuple[OverlayConfig, str]]
) -> tuple[PromptMember, ...]:
    """The ``(block, name)`` pairs ``compose_sections`` assembled, in its order."""
    from .resolver import read_rule_body

    members: list[PromptMember] = []
    if result.priorities:
        members.append(PromptMember("PRIORITIES", f"{result.name}::priorities"))

    named: dict[str, str] = {}
    for trait, text in result.trait_injections.items():
        named.setdefault(text, f"{trait}::prompt")
    if result.role_injection:
        named.setdefault(result.role_injection, f"{result.name}::prompt")
    for layer, label in overlays:
        text = layer.injection_append.strip()
        if text:
            named.setdefault(text, f"{_OVERRIDE_NS}::{label}::prompt")
    for text in result.injections:
        if not text.strip():
            continue
        if text not in named:
            raise AdaptError(
                "an injection reached the prompt that no role, trait or overlay declared"
            )
        members.append(PromptMember("body", named[text]))

    for rule in result.rules:
        if rule.source_path and read_rule_body(rule.source_path):
            members.append(PromptMember("RULES", _rule_name(rule.name)))

    for path in result.user_rules:
        try:
            body = path.read_text()
        except OSError:
            continue  # compose_sections skips it too, with its own warning
        if body.strip():
            members.append(PromptMember("USER RULES", _rule_name(path.stem)))
    return tuple(members)


def _hooks(result: CompositionResult, diagnostics: list[Diagnostic]) -> Hooks:
    """The three frontmatter channels and the check bindings, one hook per point.

    A declared payload that is missing or not executable is the author's to fix:
    it becomes a diagnostic and no hook, on every channel alike.
    """
    from ai_hats_wt import parse_worktree_carry

    from .libraries.models import SkillMetadata

    git: list[GitHook] = []
    runtime: list[RuntimeHook] = []
    worktree: list[WorktreeHook] = []

    def wired(skill_name: str, script: str, channel: str, at: str) -> bool:
        declared = result_skill_dirs[skill_name] / script
        if not declared.is_file():
            what = f"missing at {declared}"
        elif not os.access(declared, os.X_OK):
            what = f"not executable: {declared}"
        else:
            return True
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"{channel} hook {at} of {_skill_name(skill_name)}: {script} is {what}; "
                "this hook will not run in this session",
            )
        )
        return False

    result_skill_dirs = {s.name: s.source_path for s in result.skills}
    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        for event, scripts in metadata.git_hooks.items():
            for script in scripts:
                if wired(skill.name, script, "git", event):
                    git.append(GitHook(at=event, run=_payload(skill.name, script)))
        for event, hooks in metadata.runtime_hooks.items():
            for hook in hooks:
                if wired(skill.name, hook.script, "runtime", f"{event}/{hook.matcher}"):
                    runtime.append(
                        RuntimeHook(
                            at=event, matcher=hook.matcher, run=_payload(skill.name, hook.script)
                        )
                    )
        carry = parse_worktree_carry(metadata.worktree, skill.name)
        for hook in carry.wt_in:
            if wired(skill.name, hook.script, "worktree", "wt_in"):
                worktree.append(
                    WorktreeHook(at="wt_in", on=None, run=_payload(skill.name, hook.script))
                )
        for hook in carry.wt_out:
            if wired(skill.name, hook.script, "worktree", "wt_out"):
                worktree.append(
                    WorktreeHook(
                        at="wt_out", on=tuple(hook.on), run=_payload(skill.name, hook.script)
                    )
                )

    workflow = tuple(
        WorkflowHook(
            app=check.app,
            object=".".join(check.path) if check.path else None,
            at=at,
            run=f"skills/{check.run}",
            on_error=OnError(check.on_error),
        )
        for check in result.checks
        for at in check.at
    )
    return Hooks(
        git=tuple(git), runtime=tuple(runtime), workflow=workflow, worktree=tuple(worktree)
    )


def _consent(result: CompositionResult) -> tuple[Consent, ...]:
    from .check_points import selector_ends

    rows = []
    for point in result.consent:
        source, target = selector_ends(point.app, point.selector, point.path)
        rows.append(
            Consent(
                operation=".".join(point.path),
                at=point.selector,
                source=source,
                target=target,
                declared_by=point.declared_by,
                disarmed_by=None,
            )
        )
    return tuple(rows)


def _trace(
    result: CompositionResult,
    identity: str,
    resolver: LibraryResolver,
    overlays: Sequence[tuple[OverlayConfig, str]],
) -> tuple[TraceEntry, ...]:
    """Who brought each term — the same walk the composer takes, attributed.

    Traits come from the role's config and the overlays; a rule or skill is
    attributed to the first trait declaring it (the composer's first-wins
    dedup), then to the role, then to an overlay. A term an overlay removed
    keeps its entry with ``removed_by``.
    """
    role = resolver.resolve_role_config(result.name)
    traits: list[tuple[str, str]] = [
        (t, result.name) for t in (role.composition.traits if role else [])
    ]
    removed: list[TraceEntry] = []
    for layer, label in overlays:
        by = _override_name(label, identity)
        for name in layer.remove_traits:
            for i, (trait, brought_by) in enumerate(traits):
                if trait == name:
                    removed.append(TraceEntry(trait, brought_by, removed_by=by))
                    del traits[i]
                    break
        for name in layer.add_traits:
            if all(trait != name for trait, _ in traits):
                traits.append((name, by))

    brought: dict[str, str] = {}
    for trait, _ in traits:
        cfg = resolver.resolve_trait_config(trait)
        if cfg is None:
            continue
        for rule in cfg.composition.rules:
            brought.setdefault(_rule_name(rule), trait)
        for skill in cfg.composition.skills:
            brought.setdefault(_skill_name(skill), trait)
    if role is not None:
        for rule in role.composition.rules:
            brought.setdefault(_rule_name(rule), result.name)
        for skill in role.composition.skills:
            brought.setdefault(_skill_name(skill), result.name)
    for layer, label in overlays:
        by = _override_name(label, identity)
        for rule in layer.add_rules:
            brought.setdefault(_rule_name(rule), by)
        for skill in layer.add_skills:
            brought.setdefault(_skill_name(skill), by)
        for rule in layer.remove_rules:
            if _rule_name(rule) in brought:
                removed.append(
                    TraceEntry(_rule_name(rule), brought.pop(_rule_name(rule)), removed_by=by)
                )
        for skill in layer.remove_skills:
            if _skill_name(skill) in brought:
                removed.append(
                    TraceEntry(_skill_name(skill), brought.pop(_skill_name(skill)), removed_by=by)
                )

    present = [
        *(TraceEntry(trait, by, removed_by=None) for trait, by in traits),
        *(
            TraceEntry(
                _rule_name(r.name), brought.get(_rule_name(r.name), identity), removed_by=None
            )
            for r in result.rules
        ),
        *(
            TraceEntry(
                _skill_name(s.name), brought.get(_skill_name(s.name), identity), removed_by=None
            )
            for s in result.skills
        ),
    ]
    return (*present, *removed)


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


class InvalidConsentSelector(PlanRefused):
    def __init__(self, consent: Consent, reason: str) -> None:
        self.consent = consent
        super().__init__(
            f"{consent.declared_by!r}: invalid {consent.operation} selector {consent.at!r} — {reason}"
        )


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
    """Refuse a plan two entries create one target in, that writes outside its
    root without saying so, or whose consent an operation adapter cannot read."""
    from ai_hats_library.hooks.consent_gate import operations

    seen: dict[Path, int] = {}
    for entry in plan.entries:
        if entry.kind in _CREATING:
            seen[entry.target] = seen.get(entry.target, 0) + 1
    if duplicates := [target for target, n in seen.items() if n > 1]:
        raise DuplicateTargets(duplicates)
    for entry in plan.entries:
        if not entry.escape and not entry.target.is_relative_to(plan.root):
            raise EscapeUndeclared(entry.target, plan.root)
    for consent in plan.composition.consent:
        spec = operations.spec_for(consent.operation)
        if spec is None:
            raise InvalidConsentSelector(consent, "unsupported operation")
        if (reason := spec.selector_reason(consent.at)) is not None:
            raise InvalidConsentSelector(consent, reason)


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
            _PERFORM[entry.kind](entry, shadowed)


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


def _write_text(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    content, payload = cast(str, entry.content), cast(bytes, entry.bytes)
    if entry.private:
        from ai_hats_core.atomic_io import atomic_write_text

        if _same_bytes(entry.target, payload) and entry.target.stat().st_mode & 0o777 == 0o600:
            return
        atomic_write_text(entry.target, content, mode=0o600)
        return
    if _same_bytes(entry.target, payload):
        return
    _ensure_parent(entry.target)
    entry.target.write_text(content)


def _write_executable(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    content, payload = cast(str, entry.content), cast(bytes, entry.bytes)
    if not _same_bytes(entry.target, payload):
        _ensure_parent(entry.target)
        entry.target.write_text(content)
    if entry.target.stat().st_mode & 0o777 != 0o700:
        entry.target.chmod(0o700)


def _copy_tree(entry: MaterializationEntry, shadowed: set[Path]) -> None:
    source, target = cast(Path, entry.source), entry.target
    if entry.tree_digest is not None and dir_digest(source) != entry.tree_digest:
        raise StalePlan(entry)
    wanted: set[Path] = set()
    for file in sorted(p for p in source.rglob("*") if p.is_file()):
        dest = target / file.relative_to(source)
        wanted.add(dest)
        if dest in shadowed:
            continue
        if (
            dest.is_file()
            and dest.read_bytes() == file.read_bytes()
            and dest.stat().st_mode & 0o777 == file.stat().st_mode & 0o777
        ):
            continue
        _ensure_parent(dest)
        shutil.copy2(file, dest)
    if target.is_dir():
        for stray in sorted(p for p in target.rglob("*") if p.is_file()):
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
    if not json_differs(entry.target, data):
        return
    _ensure_parent(entry.target)
    entry.target.write_text(render_json(data))


def _remove_tree(entry: MaterializationEntry, _shadowed: set[Path]) -> None:
    if entry.target.is_symlink() or entry.target.exists():
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
