"""Which bytes a bound check runs, and from which root (HATS-1141, ADR-0019 D9).

Two modes, one composition. In a session the root is the surface's own mirror of
the composed skills (``Provider.session_skills_root``); outside one it is the
live composed skill. The mirror freezes bytes, not the binding list, so both
modes compose — what differs is only the root each ``ResolvedCheck.script`` is
re-based against.

HATS-1540 retired the channel's private ``<sid>/checks/`` copy: the mirror has
the same lifetime, one writer and the same TTL, and it holds EVERY composed
skill rather than only the bound ones — so a session that predates a binding
resolves instead of returning CORRUPT until restart.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats_core import CompositionResult, ResolvedCheck

    from .session_identity import SessionIdentity


#: The declaration keys in every spelling the YAML parser accepts — ``apps``,
#: ``consent`` (HATS-1682) and the retired ``checks`` alike, so a config left on
#: the old one still composes far enough to hear why it is refused (HATS-1545
#: R11). Anchored to a line start so ``prechecks:`` is not one. A scan still,
#: not a parse: a false positive costs one compose, a false negative disarms a
#: gate.
_CHECKS_KEY = re.compile(
    rb"""^[ \t]*['"]?(?:apps|checks|consent)['"]?[ \t]*:""", re.MULTILINE
)


#: "the caller did not supply one" — distinct from ``None``, which is a caller
#: stating there IS no session. Read from the environment only when a binding
#: actually needs rooting, so a project declaring no gates never pays and, more
#: importantly, never REFUSES on an environment it has no use for (HATS-1594).
FROM_ENV: Any = object()


class CheckResolutionError(Exception):
    """The check channel cannot be resolved — always a refusal, never a skip."""


def session_identity() -> SessionIdentity | None:
    """What the launching session IS, or ``None`` outside one (HATS-1594).

    ``None`` is the live-resolution mode and nothing else; an envelope that is
    present and untrustworthy raises, because reading it as absence would send
    the resolution back to the config this channel must stop asking.
    """
    from .session_identity import SessionIdentity, SessionIdentityError

    try:
        return SessionIdentity.from_env()
    except SessionIdentityError as exc:
        raise CheckResolutionError(f"checks: {exc}") from exc


def session_identity_for(project_dir: Path) -> SessionIdentity | None:
    """The session governing ``project_dir``, in this channel's error type.

    The scoped sibling of :func:`session_identity`, for the consumers that hold
    the project they are gating. Unscoped, a session of ANOTHER project chose
    which bindings fired here, and a role declaring none disarmed the gate in
    silence (HATS-1631). Raising stays this module's contract so a torn envelope
    is still a refusal at the consumer, never a skip.
    """
    from .session_identity import SessionIdentityError, identity_for_project

    try:
        return identity_for_project(project_dir)
    except SessionIdentityError as exc:
        raise CheckResolutionError(f"checks: {exc}") from exc


def resolve_carried_checks(
    project_dir: Path,
    app: str,
    *,
    identity: SessionIdentity | None | Any = FROM_ENV,
    compose: Callable[[Path], CompositionResult | None] | None = None,
) -> tuple[ResolvedCheck, ...]:
    """Every row declared under ``app``, re-based onto its root.

    The carrier half of ADR-0019 D11: which of these the caller subscribes to is
    the caller's decision, made against the topology it runs. The integration
    that owns ``app`` names it here, so a row written for another application is
    never handed to this one — and a broken row of one app cannot abort
    another's event (HATS-1545).
    """
    result, identity = _composed(project_dir, identity, compose)
    if result is None:
        return ()
    checks = tuple(check for check in result.checks if check.app == app)
    if not checks:
        return ()
    return _rooted(result, checks, identity)


def resolve_checks_at(
    project_dir: Path,
    app: str,
    point: str,
    *,
    identity: SessionIdentity | None | Any = FROM_ENV,
    compose: Callable[[Path], CompositionResult | None] | None = None,
) -> tuple[ResolvedCheck, ...]:
    """Every row of ``app`` bound to one point, re-based onto its root.

    The sibling of :func:`resolve_carried_checks` for the apps ai-hats fires
    itself (HATS-1540): one point, named by the call site that fires it, and
    drawn from the cargo ai-hats validates itself (``_OWNED_POINTS``).

    ``app`` is a parameter rather than the hardcoded ``wt`` it was through
    HATS-1581, because ai-hats now fires two apps and nothing stops them from
    spelling a point alike — filtering on ``at`` alone would cross the wires.
    """
    result, identity = _composed(project_dir, identity, compose)
    if result is None:
        return ()
    checks = tuple(check for check in result.checks if check.app == app and point in check.at)
    if not checks:
        return ()
    return _rooted(result, checks, identity)


def _rooted(
    result: CompositionResult,
    checks: tuple[ResolvedCheck, ...],
    identity: SessionIdentity | None,
) -> tuple[ResolvedCheck, ...]:
    """Pick the root every one of ``checks`` runs from — the mode split."""
    # D9 clause 4 first, over every binding: a source inside a linked worktree is
    # refused before the mirror is even located, so the message names the tree
    # rather than whatever the surface lookup happens to say.
    for check in checks:
        reject_worktree_root(check.script_path, check)
    if identity is None:
        return checks
    mirror = mirror_for(_mirror_root(identity), result)
    return tuple(rebase_onto_mirror(check, mirror) for check in checks)


@dataclass(frozen=True)
class _Mirror:
    """The session's skill mirror: its root, and how it names each leaf.

    Two conventions met here before HATS-1540 — every surface writes the leaf as
    the composed skill's raw ``name`` while this module re-derived it with
    ``resolve_namespace``, so a namespaced skill (``dev::python`` against
    ``dev/python``) resolved to a directory no surface had written. ``leaf`` maps
    the binding's spelling onto the mirror's, making the mirror the authority.
    """  # comment-length: allow — the divergence is why this type exists

    root: Path
    leaf: dict[str, str]


def mirror_for(root: Path, result: CompositionResult) -> _Mirror:
    """The mirror at ``root``, with the leaf spelling this composition dictates.

    Public because the launch report resolves the same way off a root it already
    holds (HATS-1548) — it must not take a second surface lookup.
    """
    from .libraries.models import resolve_namespace

    return _Mirror(
        root=root,
        leaf={resolve_namespace(skill.name): skill.name for skill in result.skills},
    )


def _mirror_root(identity: SessionIdentity) -> Path:
    """The mirror the session actually wrote — read, not re-resolved (HATS-1594).

    The surface decided this once at launch, holding its own provider object.
    Asking the registry again here is what made a gate resolve against claude's
    mirror in a session running under agy; an empty value is that surface saying
    it mirrors nothing, which leaves a binding no bytes and so must refuse.
    """
    if not identity.skills_root:
        raise CheckResolutionError(
            f"checks: the surface running session {identity.id!r} ({identity.provider}) "
            f"mirrors no skills, so a binding has no bytes to run — run outside a session "
            f"or use a surface that materializes skills"
        )
    return Path(identity.skills_root)


def absent_bytes_notice(identity: SessionIdentity | None) -> str:
    """Why a bound script's bytes were not there — the D9 answer, in the
    operator's terms.

    Read off the identity the session carries (HATS-1594), never re-derived: the
    surface and the mirror were decided once at launch, and asking the registry
    again here is what made a gate resolve against another surface's mirror.
    """
    if identity is None:
        return (
            "it resolved against the live library, where the file is simply not "
            "there — the declaring skill no longer ships it"
        )
    return (
        f"session {identity.id!r} executes bytes frozen at launch: its {identity.provider} "
        f"mirror at {identity.skills_root} holds no such file, and the live library is NOT "
        f"used as a fallback (ADR-0019 D9). Restart the session so it mirrors this binding, "
        f"or run this transition outside a session."
    )


def rebase_onto_mirror(check: ResolvedCheck, mirror: _Mirror) -> ResolvedCheck:
    """The composition names ``{skill, script}``; this picks the root.

    In a session that is the mirror, and a mirror that is not there stays the
    answer — re-resolving live would disarm the isolation (R10).
    """
    from .libraries.models import resolve_namespace

    leaf = mirror.leaf.get(resolve_namespace(check.skill))
    if leaf is None:
        raise CheckResolutionError(
            f"checks: {check.declared_by!r} binds {check.skill}/{check.script}, but no "
            f"composed skill answers to {check.skill!r} — the mirror under {mirror.root} "
            f"can hold no directory for it"
        )
    root = (mirror.root / leaf).resolve()
    script_path = (root / check.script).resolve()
    if not script_path.is_relative_to(root):
        raise CheckResolutionError(
            f"checks: {check.declared_by!r} binds {check.skill}/{check.script}, which "
            f"resolves to {script_path} — outside this session's mirror root {root}"
        )
    # HATS-1651: the one place both paths exist at once. Keeping the live one
    # does not soften R10 — nothing re-resolves against it — it only lets a
    # refusal say whether the frozen bytes still match the library's.
    return replace(check, script_path=script_path, source_path=check.script_path)


def reject_worktree_root(script_path: Path, check: ResolvedCheck) -> None:
    """D9 clause 4: a linked worktree is never a resolution root — a gate must
    not run the half-written copy of itself that lives on the branch it judges.

    Guards the path the COMPOSITION resolved, in both modes and before the root
    is picked: a snapshot copies whatever ``source_path`` points at, so checking
    the rebased path would leave a session started inside a worktree running
    that branch's frozen bytes — and would inspect the cache root, which is
    outside every checkout and therefore never inside anything.
    """  # comment-length: allow — clause 2 not discharging clause 4 is the whole point
    for parent in script_path.parents:
        marker = parent / ".git"
        if not marker.exists():
            continue
        if marker.is_dir():
            return  # a main checkout — the ordinary case
        if not _is_worktree_marker(marker, check):
            continue  # a submodule; the enclosing checkout is still to come
        raise CheckResolutionError(
            f"checks: {check.declared_by!r} binds {check.skill}/{check.script} to "
            f"{script_path}, inside the linked worktree {parent} — a worktree is never a "
            f"resolution root (ADR-0019 D9 clause 4)"
        )


def _is_worktree_marker(marker: Path, check: ResolvedCheck) -> bool:
    """A ``.git`` FILE is a linked worktree OR a submodule working tree.

    Git writes the admin dir as ``<common>/worktrees/<id>`` for the first and
    ``<common>/modules/<path>`` for the second, so the segment above the target
    separates them from one file read — no ``rev-parse`` subprocess in the
    in-lock path, and an answer even where git would refuse to give one. A
    submodule is a vendored dependency, not a task branch, so it resolves.
    """  # comment-length: allow — the layout IS the discriminator
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CheckResolutionError(
            f"checks: {check.declared_by!r} binds {check.skill}/{check.script} under {marker}, "
            f"which cannot be read ({exc}) — so whether that is a linked worktree cannot be "
            f"told, and D9 clause 4 cannot be honoured"
        ) from exc
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            return Path(line.partition(":")[2].strip()).parent.name == "worktrees"
    raise CheckResolutionError(
        f"checks: {check.declared_by!r} binds {check.skill}/{check.script} under {marker}, a "
        f".git file carrying no 'gitdir:' line — whether that is a linked worktree cannot be told"
    )


# HATS-1541 retired `_guard_topology`. It existed to NAME the divergence between
# the packaged catalog ai-hats validated against and the topology the kernel ran
# — and could do nothing else, because it could not tell a typo from a point
# addressed to a sibling backlog. Both halves are gone with the catalog: the
# owner of a topology now filters its own points (ADR-0019 D11).


def _library_roots(project_dir: Path) -> list[Path]:
    """The roots a composition could draw a declaration from — every one of them.

    Derived exactly the way ``Assembler`` derives them, ``local_libraries``
    included: a root the probe cannot see hides a binding that composes for
    real, and the probe then reports "nothing declared" (R3). Not re-pointing
    here would not keep a worktree from being a resolution root — the assembler
    re-points during the compose regardless; it would only blind the probe. D9
    clause 4 is enforced where it can be, in ``reject_worktree_root``.
    """  # comment-length: allow — this divergence was the HYP-078 hole, twice
    from .library_paths import build_library_paths, worktree_local_libraries
    from .models import ProjectConfig
    from .paths.constants import PROJECT_CONFIG

    config = ProjectConfig.from_yaml(project_dir / PROJECT_CONFIG)
    return build_library_paths(
        project_dir,
        config_paths=config.library_paths,
        local_libraries=worktree_local_libraries(project_dir),
    )


def declares_checks(project_dir: Path) -> bool:
    """Whether any trait or role in reach declares ``apps`` or ``consent``.

    A byte scan, not a parse, so a project with no bindings does not compose on
    every transition (S3). Only traits and roles are read — the two the composer
    collects from. The margin is real but modest, and it shrinks where it is
    needed least: both this and the compose resolve the same library roots, and
    outside ``project_dir`` that costs git subprocesses neither can skip.
    """
    for root in _library_roots(project_dir):
        for kind in ("traits", "roles"):
            base = root / kind
            if not base.is_dir():
                continue
            for config in _component_configs(base):
                data = config.read_bytes()
                # memchr throws out the files with no declaration at all first
                if (
                    b"apps" in data or b"checks" in data or b"consent" in data
                ) and _CHECKS_KEY.search(data):
                    return True
    return False


def _component_configs(base: Path) -> Iterator[Path]:
    """Every ``config.yaml`` under ``base``, through symlinked directories too.

    ``find_component_dir`` reaches a symlinked trait via ``is_dir()``, which
    follows, so a component shared by symlink composes for real; a scan that
    stops at the link would report its declaration as absent. ``os.walk`` is the
    3.11-compatible way to follow — ``Path.rglob(recurse_symlinks=…)`` is 3.13+.
    Errors are raised, never walked past: an unreadable subtree may hold the
    declaration, so skipping it would answer ``False`` without knowing.
    """  # comment-length: allow — both halves are silent-skip holes this closed
    seen: set[tuple[int, int]] = set()
    _unvisited(base, seen)
    for dirpath, dirnames, filenames in os.walk(base, onerror=_reraise, followlinks=True):
        # Following links buys their cycles; identity, not path, ends the walk.
        dirnames[:] = [name for name in dirnames if _unvisited(Path(dirpath, name), seen)]
        if "config.yaml" in filenames:
            yield Path(dirpath, "config.yaml")


def _unvisited(directory: Path, seen: set[tuple[int, int]]) -> bool:
    info = directory.stat()
    key = (info.st_dev, info.st_ino)
    if key in seen:
        return False
    seen.add(key)
    return True


def _reraise(exc: OSError) -> None:
    raise exc


def _compose_role(project_dir: Path, identity: SessionIdentity | None) -> CompositionResult | None:
    """The session's live composition — the binding list in BOTH modes.

    Through the seam: the composition layer is integrator-only (HATS-865), and
    this module is a consumer of it, not a member. The role comes from the
    session when there is one, and only outside a session from the config —
    where ``active_role`` genuinely is the answer. ``None`` when no role is set.

    Live, never frozen: HATS-1540 retired the snapshotted binding list so a
    session that predates a binding still resolves it. Only the identity is
    fixed at launch.
    """  # comment-length: allow — which half is frozen is the contract
    from .composition_seam import compose_for_checks

    return compose_for_checks(project_dir, role=identity.role if identity else None)


def _compose_fail_closed(
    project_dir: Path, identity: SessionIdentity | None | Any = FROM_ENV
) -> tuple[CompositionResult | None, SessionIdentity | None]:
    """``None`` iff nothing is declared. Any other trouble raises — the
    fail-open ``compose_for_carry`` is right for carry and is HYP-078 here.

    The probe is inside the boundary, not before it: it reads ``ai-hats.yaml``
    and walks library roots, so it raises config and OS errors of its own (R8).
    A probe that cannot finish has not established that nothing is declared —
    only that it cannot tell — and this channel refuses on that, deliberately
    stricter than ``load_root``, which defaults a broken config through for
    read-only verbs.

    The probe answers "is a declaration in reach", not "does THIS project use
    one", and once a builtin trait or role ships a binding it answers ``True``
    everywhere. So the no-role composition is the second ``None``, and it has to
    be: otherwise a builtin binding would refuse every transition of every
    role-less project on that build (HATS-1137).
    """  # comment-length: allow — the asymmetry with the rest of rack is a decision
    try:
        declared = declares_checks(project_dir)
    except Exception as exc:
        raise CheckResolutionError(
            f"whether any check is declared could not be determined ({type(exc).__name__}): {exc}"
        ) from exc
    if not declared:
        # Nothing to root, so nothing to be told: a project with no bindings must
        # not be refused over a session envelope it has no use for (HATS-1594).
        return None, None
    if identity is FROM_ENV:
        identity = session_identity()
    try:
        result = _compose_role(project_dir, identity)
    except Exception as exc:
        raise CheckResolutionError(
            f"checks are declared but the role could not be composed ({type(exc).__name__}): {exc}"
        ) from exc
    return result, identity


def resolve_consent_points(
    project_dir: Path,
    app: str,
    *,
    path: tuple[str, ...] = (),
    identity: SessionIdentity | None | Any = FROM_ENV,
    compose: Callable[[Path], CompositionResult | None] | None = None,
) -> frozenset[str]:
    """Points of ``app`` this project's role wants the supervisor asked on.

    No re-basing and no worktree clause, unlike its ``resolve_checks_at``
    sibling: a consent point names no script, so there are no bytes to run from
    the wrong tree and nothing a linked worktree could poison (HATS-1682).
    """
    result, _identity = _composed(project_dir, identity, compose)
    if result is None:
        return frozenset()
    return frozenset(
        point.point
        for point in result.consent
        if point.app == app and (not path or point.path == path)
    )


def _composed(
    project_dir: Path,
    identity: SessionIdentity | None | Any,
    compose: Callable[[Path], CompositionResult | None] | None,
) -> tuple[CompositionResult | None, SessionIdentity | None]:
    """The binding list, from whoever holds it — and never a broken one.

    The ``result.errors`` refusal lives HERE rather than inside
    ``_compose_fail_closed`` because it is a property of the channel, not of one
    way of obtaining a composition. An in-process caller passing ``compose``
    (HATS-1594) would otherwise skip it, and a role whose composition reported
    errors would arm nothing while looking armed — which is the whole defect
    class this channel exists to remove.
    """  # comment-length: allow — why the check is not in the composer
    if compose:
        result = compose(project_dir)
        identity = None if identity is FROM_ENV else identity
    else:
        result, identity = _compose_fail_closed(project_dir, identity)
    if result is not None and result.errors:
        raise CheckResolutionError(
            f"checks are declared but composing role {result.name!r} reported "
            f"{result.errors} — a gate cannot be installed from a broken composition"
        )
    return result, identity


__all__ = [
    "CheckResolutionError",
    "declares_checks",
    "resolve_carried_checks",
    "resolve_checks_at",
    "session_identity",
]
