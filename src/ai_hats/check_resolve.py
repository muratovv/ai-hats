"""Which bytes a bound check runs, and from which root (HATS-1141, ADR-0019 D9).

Two modes, one composition. In a session the root is ai-hats's own snapshot
(``<sid>/checks/<skill>/``); outside one it is the live composed skill. The
snapshot freezes bytes, not the binding list, so both modes compose — what
differs is only the root each ``ResolvedCheck.script`` is re-based against.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from ai_hats_observe.trace import ENV_SESSION_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats_core import CompositionResult, ResolvedCheck
    from ai_hats_rack.fsm import Topology


_EDGE_PREFIX = "edge:"


class CheckResolutionError(Exception):
    """The check channel cannot be resolved — always a refusal, never a skip."""


def session_id() -> str:
    """The launching session, or ``""`` outside one (the live-resolution mode)."""
    return os.environ.get(ENV_SESSION_ID, "")


def resolve_edge_checks(
    project_dir: Path,
    *,
    topology: Topology,
    session_id: str = "",
    compose: Callable[[Path], CompositionResult | None] | None = None,
) -> tuple[ResolvedCheck, ...]:
    """Every ``edge:`` binding this project declares, re-based onto its root."""
    result = (compose or _compose_fail_closed)(project_dir)
    if result is None:
        return ()
    checks = tuple(check for check in result.checks if check.point.startswith(_EDGE_PREFIX))
    if not checks:
        return ()
    _guard_topology(checks, topology)
    return tuple(_rebased(project_dir, check, session_id) for check in checks)


def _rebased(project_dir: Path, check: ResolvedCheck, session_id: str) -> ResolvedCheck:
    """The composition names ``{skill, script}``; this picks the root.

    In a session that is the snapshot, and a snapshot that is not there stays
    the answer — re-resolving live would disarm the isolation (R10).
    """
    _reject_worktree_root(check.script_path, check)
    if not session_id:
        return check

    from .libraries.models import resolve_namespace
    from .paths import session_checks_dir

    root = (session_checks_dir(project_dir, session_id) / resolve_namespace(check.skill)).resolve()
    script_path = (root / check.script).resolve()
    if not script_path.is_relative_to(root):
        raise CheckResolutionError(
            f"checks: {check.declared_by!r} binds {check.skill}/{check.script}, which "
            f"resolves to {script_path} — outside this session's snapshot root {root}"
        )
    return replace(check, script_path=script_path)


def _reject_worktree_root(script_path: Path, check: ResolvedCheck) -> None:
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


def _guard_topology(checks: tuple[ResolvedCheck, ...], topology: Topology) -> None:
    """R7: the kernel's topology comes through the seam and is authoritative.

    A point the running topology has no edge for would just never match — the
    silent skip this channel exists to remove — so name the divergence, and both
    of its sides. Sibling catalogs are NOT taught to ``known_points()`` here.
    """
    from ai_hats_rack.fsm import all_edge_keys

    stray = sorted(
        {check.point for check in checks if check.point not in set(all_edge_keys(topology))}
    )
    if not stray:
        return
    from ai_hats_rack import load_backlog

    catalog = load_backlog().topology
    raise CheckResolutionError(
        f"bound point(s) {stray} are not edges of the topology this backlog runs "
        f"(states {list(topology.states)}); they were validated against the packaged "
        f"catalog 'ai_hats_rack/backlog.yaml' (states {list(catalog.states)}). The two "
        f"topologies diverge, so the gate could never fire here (ADR-0019 D3)"
    )


def _library_roots(project_dir: Path) -> list[Path]:
    """The roots a composition could draw a declaration from — every one of them.

    Derived exactly the way ``Assembler`` derives them, ``local_libraries``
    included: a root the probe cannot see hides a binding that composes for
    real, and the probe then reports "nothing declared" (R3). Not re-pointing
    here would not keep a worktree from being a resolution root — the assembler
    re-points during the compose regardless; it would only blind the probe. D9
    clause 4 is enforced where it can be, in ``_reject_worktree_root``.
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
    """Whether any trait or role in reach declares ``composition.checks``.

    A byte scan, not a parse: composing costs an order of magnitude more, and a
    project with no bindings must not pay it on every transition (S3). Only
    traits and roles are read — those are the two the composer collects from.
    """
    for root in _library_roots(project_dir):
        for kind in ("traits", "roles"):
            base = root / kind
            if not base.is_dir():
                continue
            for config in _component_configs(base):
                if b"checks:" in config.read_bytes():
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


def _compose_role(project_dir: Path) -> CompositionResult:
    """The active role's live composition — the binding list in BOTH modes.

    Through the seam: the composition layer is integrator-only (HATS-865), and
    this module is a consumer of it, not a member.
    """
    from .composition_seam import compose_for_checks

    return compose_for_checks(project_dir)


def _compose_fail_closed(project_dir: Path) -> CompositionResult | None:
    """``None`` iff nothing is declared. Any other trouble raises — the
    fail-open ``compose_for_carry`` is right for carry and is HYP-078 here.

    The probe is inside the boundary, not before it: it reads ``ai-hats.yaml``
    and walks library roots, so it raises config and OS errors of its own (R8).
    A probe that cannot finish has not established that nothing is declared —
    only that it cannot tell — and this channel refuses on that, deliberately
    stricter than ``load_root``, which defaults a broken config through for
    read-only verbs.
    """  # comment-length: allow — the asymmetry with the rest of rack is a decision
    try:
        declared = declares_checks(project_dir)
    except Exception as exc:
        raise CheckResolutionError(
            f"whether any check is declared could not be determined ({type(exc).__name__}): {exc}"
        ) from exc
    if not declared:
        return None
    try:
        result = _compose_role(project_dir)
    except Exception as exc:
        raise CheckResolutionError(
            f"checks are declared but the role could not be composed ({type(exc).__name__}): {exc}"
        ) from exc
    if result.errors:
        raise CheckResolutionError(
            f"checks are declared but composing role {result.name!r} reported "
            f"{result.errors} — a gate cannot be installed from a broken composition"
        )
    return result


__all__ = ["CheckResolutionError", "declares_checks", "resolve_edge_checks", "session_id"]
