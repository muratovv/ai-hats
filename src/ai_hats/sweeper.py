"""Generic unclaimed-marker sweeper (HATS-905).

Colocated markers are the source of truth for what a mechanism materialized
outside ``<ai_hats_dir>``. When a marker's ``owner_key`` is absent from the
living-owner registry (``ai_hats.owners``) the mechanism is dead — the marker
victims are swept, but only entries whose CONTENT is proven engine-owned:
hash recorded in the marker, an embedded ownership string, or the shipped
legacy semantics of the two pre-HATS-905 dead surfaces.

Marker convention (HATS-911): new line-manifest markers are written via
:func:`write_marker` — ``# ai-hats-owner: <owner_key>`` header, then one
``<sha256-12>  <relpath>`` line per owned entry (dirs hash via
``plugin_dir._dir_digest``). The hash is the content-proof: sweep discards an
entry only while its on-disk content still matches; user-modified files are
kept with a WARN. ``#`` lines are comments, so hash-less readers stay compatible.
"""  # comment-length: allow — marker-format contract (HATS-911)

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ai_hats_core.safe_delete import discard, replace

from . import owners
from .paths import AI_HATS_MANAGED_MARKER, claude_dir, claude_settings_json, claude_skills_dir
from .plugin_dir import _dir_digest, _is_safe_relative

_SETTINGS_RELPATH = str(claude_settings_json(Path(".")))

OWNER_HEADER_PREFIX = "# ai-hats-owner:"
_DIGEST_LEN = 12


@dataclass(frozen=True)
class LineManifestSurface:
    """One known marker location. Append-only engine data: locations are
    forever, owners die (a dead mechanism cannot describe its own marker)."""

    owner_key: str
    marker_relpath: str
    legacy: bool = False
    embedded_marker: str | None = None


@dataclass(frozen=True)
class SettingsTagsSurface:
    """Managed-tag entries inside ``.claude/settings.json``. The
    ``_ai_hats_managed`` tag is the embedded ownership proof (same semantics
    as the live sweep, HATS-833)."""

    owner_key: str
    settings_relpath: str = _SETTINGS_RELPATH
    tag_prefix: str = "ai-hats:"


@dataclass(frozen=True)
class ProcSurface:
    """A dead surface whose validated sweep procedure predates the generic
    sweeper — one procedure, every caller shares it (HATS-907 absorb contract)."""

    owner_key: str
    marker_relpath: str
    proc: Callable[[Path], list[str]]


@dataclass(frozen=True)
class SurfaceSweep:
    owner_key: str
    marker: Path
    swept: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    refused: str | None = None
    marker_removed: bool = False


@dataclass(frozen=True)
class _Entry:
    name: str
    digest: str | None
    raw: str


Surface = LineManifestSurface | SettingsTagsSurface | ProcSurface


def default_surfaces() -> tuple[Surface, ...]:
    """Known marker locations — append-only: a location outlives its owner.

    Imports every registering module itself: liveness must never depend on
    what the caller happened to import earlier (wrong-sweep of a live surface).
    """
    from . import providers  # noqa: F401 — registers runtime-hooks
    from .hooks_manager import (
        GITHOOKS_DIR,
        GITHOOKS_DISPATCHER_MARKER,
        GITHOOKS_MANIFEST,
    )
    from .plugin_dir import drop_legacy_claude_publish, drop_legacy_skills_mirror

    skills_marker = claude_skills_dir(Path(".")) / AI_HATS_MANAGED_MARKER
    publish_marker = claude_dir(Path(".")) / AI_HATS_MANAGED_MARKER
    return (
        LineManifestSurface(
            owner_key="git-hooks",
            marker_relpath=f"{GITHOOKS_DIR}/{GITHOOKS_MANIFEST}",
            embedded_marker=GITHOOKS_DISPATCHER_MARKER,
        ),
        SettingsTagsSurface(owner_key="runtime-hooks"),
        ProcSurface(
            owner_key="skills-export",
            marker_relpath=str(skills_marker),
            proc=drop_legacy_skills_mirror,
        ),
        ProcSurface(
            owner_key="claude-publish",
            marker_relpath=str(publish_marker),
            proc=drop_legacy_claude_publish,
        ),
    )


def sweep_unclaimed(
    project_dir: Path,
    *,
    surfaces: tuple[Surface, ...],
    dry_run: bool = False,
) -> list[SurfaceSweep]:
    """Sweep every known surface whose marker names a dead owner.

    Living owners are skipped silently; surfaces without a marker on disk
    produce no report. Returns one ``SurfaceSweep`` per acted-on marker.
    ``dry_run`` reports what WOULD happen without touching disk (the
    deferred-sweep WARN path: version skew / hard-delete mode).
    """
    reports: list[SurfaceSweep] = []
    for surface in surfaces:
        if isinstance(surface, SettingsTagsSurface):
            report = _sweep_settings_tags(project_dir, surface, dry_run=dry_run)
        elif isinstance(surface, ProcSurface):
            report = _sweep_proc(project_dir, surface, dry_run=dry_run)
        else:
            marker = project_dir / Path(surface.marker_relpath)
            if not marker.is_file():
                continue
            report = _sweep_line_manifest(project_dir, surface, marker, dry_run=dry_run)
        if report is not None:
            reports.append(report)
    return reports


def _sweep_proc(
    project_dir: Path, surface: ProcSurface, *, dry_run: bool = False
) -> SurfaceSweep | None:
    marker = project_dir / Path(surface.marker_relpath)
    if not marker.is_file():
        return None
    if owners.is_living(surface.owner_key):
        return None
    if dry_run:
        return SurfaceSweep(owner_key=surface.owner_key, marker=marker)
    try:
        removed = surface.proc(project_dir)
    except Exception as exc:  # noqa: BLE001 — one crashing surface must not abort the bump
        return SurfaceSweep(
            owner_key=surface.owner_key,
            marker=marker,
            refused=f"sweep procedure crashed: {exc}",
        )
    return SurfaceSweep(
        owner_key=surface.owner_key,
        marker=marker,
        swept=tuple(removed),
        marker_removed=not marker.exists(),
    )


def _sweep_settings_tags(
    project_dir: Path,
    surface: SettingsTagsSurface,
    *,
    dry_run: bool = False,
) -> SurfaceSweep | None:
    settings_path = project_dir / Path(surface.settings_relpath)
    if not settings_path.is_file():
        return None
    try:
        data = json.loads(settings_path.read_text())
    except (ValueError, OSError):
        return SurfaceSweep(
            owner_key=surface.owner_key,
            marker=settings_path,
            refused="settings.json unreadable/unparseable — surface refused",
        )
    hooks_root = data.get("hooks")
    if not isinstance(hooks_root, dict):
        return None
    if not _has_tagged_entries(hooks_root, surface.tag_prefix):
        return None
    if owners.is_living(surface.owner_key):
        return None

    # Same removal semantics as the live sweep — empty desired set drops
    # every ai-hats-tagged entry; user-authored entries survive.
    from .providers import ClaudeProvider

    removed = ClaudeProvider._sweep_stale_managed_tags(hooks_root, set())
    if dry_run:
        return SurfaceSweep(
            owner_key=surface.owner_key,
            marker=settings_path,
            swept=tuple(sorted(removed)),
        )
    if not hooks_root:
        del data["hooks"]
    replace(
        settings_path,
        (json.dumps(data, indent=2) + "\n").encode(),
        reason=f"unclaimed-marker:{surface.owner_key}",
        project_dir=project_dir,
    )
    return SurfaceSweep(
        owner_key=surface.owner_key,
        marker=settings_path,
        swept=tuple(sorted(removed)),
    )


def _has_tagged_entries(hooks_root: dict, tag_prefix: str) -> bool:
    for event_list in hooks_root.values():
        if not isinstance(event_list, list):
            continue
        for entry in event_list:
            if isinstance(entry, dict) and str(
                entry.get("_ai_hats_managed", "")
            ).startswith(tag_prefix):
                return True
    return False


def _sweep_line_manifest(
    project_dir: Path,
    surface: LineManifestSurface,
    marker: Path,
    *,
    dry_run: bool = False,
) -> SurfaceSweep | None:
    try:
        owner_key, entries = _parse_marker(marker, surface)
    except _MarkerRefused as exc:
        return SurfaceSweep(
            owner_key=surface.owner_key, marker=marker, refused=str(exc)
        )
    if owners.is_living(owner_key):
        return None

    base_dir = marker.parent
    for entry in entries:
        if not _is_safe_relative(base_dir, entry.name):
            return SurfaceSweep(
                owner_key=owner_key,
                marker=marker,
                refused=f"unsafe entry {entry.raw!r} — marker refused",
            )

    reason = f"unclaimed-marker:{owner_key}"
    swept, kept, resolved_raws = _discard_proven(
        entries, base_dir, surface, reason, project_dir, dry_run=dry_run
    )
    if dry_run:
        return SurfaceSweep(
            owner_key=owner_key,
            marker=marker,
            swept=tuple(swept),
            kept=tuple(entry.name for entry in kept),
        )
    marker_removed = _shrink_marker(
        marker, resolved_raws, has_kept=bool(kept), reason=reason, project_dir=project_dir
    )
    return SurfaceSweep(
        owner_key=owner_key,
        marker=marker,
        swept=tuple(swept),
        kept=tuple(entry.name for entry in kept),
        marker_removed=marker_removed,
    )


def _discard_proven(
    entries: list[_Entry],
    base_dir: Path,
    surface: LineManifestSurface,
    reason: str,
    project_dir: Path,
    *,
    dry_run: bool,
) -> tuple[list[str], list[_Entry], set[str]]:
    """Content-proof decision loop: proven → trash, unproven → kept."""
    swept: list[str] = []
    kept: list[_Entry] = []
    resolved_raws: set[str] = set()
    for entry in entries:
        victim = base_dir / entry.name
        if not victim.exists() and not victim.is_symlink():
            resolved_raws.add(entry.raw)
            continue
        if _content_proven(victim, entry, surface):
            if not dry_run:
                discard(victim, reason=reason, project_dir=project_dir)
            swept.append(entry.name)
            resolved_raws.add(entry.raw)
        else:
            kept.append(entry)
    return swept, kept, resolved_raws


def _shrink_marker(
    marker: Path,
    resolved_raws: set[str],
    *,
    has_kept: bool,
    reason: str,
    project_dir: Path,
) -> bool:
    """Marker maintenance: verbatim minus resolved lines; fully resolved → trash.

    Verbatim rewrite keeps header/comments/format of the contested remainder
    exactly as the dead mechanism wrote them."""
    if not has_kept:
        discard(marker, reason=reason, project_dir=project_dir)
        return True
    remaining = [
        raw for raw in marker.read_text().splitlines() if raw.strip() not in resolved_raws
    ]
    replace(
        marker,
        ("\n".join(remaining) + "\n").encode(),
        reason=reason,
        project_dir=project_dir,
    )
    return False


class _MarkerRefused(Exception):
    """Marker is structurally untrustworthy — refuse the whole surface."""


def _parse_marker(
    marker: Path, surface: LineManifestSurface
) -> tuple[str, list[_Entry]]:
    owner_key: str | None = None
    entries: list[_Entry] = []
    for raw in marker.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(OWNER_HEADER_PREFIX):
            owner_key = line[len(OWNER_HEADER_PREFIX) :].strip()
            if not owner_key:
                raise _MarkerRefused("empty ai-hats-owner header")
            continue
        if line.startswith("#"):
            continue
        entries.append(_parse_entry(line, hashed=owner_key is not None))
    return owner_key or surface.owner_key, entries


def read_marker_names(path: Path) -> set[str]:
    """Entry names from a line-manifest marker, tolerating both formats.

    Reader counterpart of :func:`write_marker` for LIVING mechanisms (drift
    detect, cleanup): hash column stripped after an owner header, plain
    name-per-line otherwise. A malformed hashed line falls back to the raw
    line — readers self-heal on the next rematerialization, only the sweeper
    refuses."""
    if not path.exists():
        return set()
    names: set[str] = set()
    hashed = False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(OWNER_HEADER_PREFIX):
            hashed = True
            continue
        if line.startswith("#"):
            continue
        try:
            names.add(_parse_entry(line, hashed=hashed).name)
        except _MarkerRefused:
            names.add(line)
    return names


def _parse_entry(line: str, *, hashed: bool) -> _Entry:
    if not hashed:
        return _Entry(name=line, digest=None, raw=line)
    digest, sep, name = line.partition("  ")
    name = name.strip()
    if not sep or not name or len(digest) != _DIGEST_LEN or not _is_hex(digest):
        raise _MarkerRefused(f"malformed hashed entry {line!r}")
    return _Entry(name=name, digest=digest, raw=line)


def _is_hex(token: str) -> bool:
    try:
        int(token, 16)
    except ValueError:
        return False
    return True


def _content_proven(
    victim: Path, entry: _Entry, surface: LineManifestSurface
) -> bool:
    if surface.legacy:
        return True
    if entry.digest is not None:
        return _digest_of(victim) == entry.digest
    if surface.embedded_marker is not None and victim.is_file():
        try:
            return surface.embedded_marker in victim.read_text()
        except (OSError, UnicodeDecodeError):
            return False
    return False


def _digest_of(victim: Path) -> str:
    if victim.is_dir():
        return _dir_digest(victim)[:_DIGEST_LEN]
    try:
        return hashlib.sha256(victim.read_bytes()).hexdigest()[:_DIGEST_LEN]
    except OSError:
        return ""


def write_marker(
    marker: Path,
    *,
    owner_key: str,
    names: Iterable[str],
    project_dir: Path,
    reason: str,
) -> None:
    """Write ``marker`` in the hashed owner_key convention (module docstring).

    Hashes are read from disk AFTER materialization — the marker always
    proves the content that is actually there. Empty ``names`` removes the
    marker (nothing left to own). An entry missing on disk or escaping the
    marker's directory is a programmer error — fail loud, never write a
    marker the sweeper would refuse or mis-prove.
    """
    if not owner_key.strip():
        raise ValueError("write_marker: owner_key must be non-empty")
    entries = sorted(set(names))
    if not entries:
        if marker.exists():
            discard(marker, reason=reason, project_dir=project_dir)
        return
    lines = [f"{OWNER_HEADER_PREFIX} {owner_key}"]
    for name in entries:
        if not _is_safe_relative(marker.parent, name):
            raise ValueError(f"write_marker: unsafe entry {name!r}")
        digest = _digest_of(marker.parent / name)
        if not digest:
            raise ValueError(
                f"write_marker: entry {name!r} not materialized at {marker.parent / name}"
            )
        lines.append(f"{digest}  {name}")
    replace(
        marker,
        ("\n".join(lines) + "\n").encode(),
        reason=reason,
        project_dir=project_dir,
    )


def run_unclaimed_sweep(project_dir: Path, *, binary_behind: bool) -> None:
    """Install-time entry point: gates, sweep, user-facing report (HATS-905).

    A stale binary must not judge liveness (version skew), and without a
    trash session there is no undo — both defer with a WARN naming the
    unclaimed marker.
    """
    from ai_hats_core.safe_delete import hard_delete_mode

    surfaces = default_surfaces()
    if binary_behind:
        deferred = "binary behind upstream — run 'ai-hats self update'"
    elif hard_delete_mode():
        deferred = "hard-delete mode (AI_HATS_TRASH_DIR=-): no undo path"
    else:
        deferred = None

    if deferred is not None:
        reports = sweep_unclaimed(project_dir, surfaces=surfaces, dry_run=True)
        _report_deferred(reports, deferred)
        return
    _report_swept(sweep_unclaimed(project_dir, surfaces=surfaces))


def _report_deferred(reports: list[SurfaceSweep], reason: str) -> None:
    for report in reports:
        print(
            f"[ai-hats] WARN: unclaimed marker {report.marker} "
            f"(owner '{report.owner_key}') — sweep deferred: {reason}",
            file=sys.stderr,
        )


def _report_swept(reports: list[SurfaceSweep]) -> None:
    from ai_hats_core.safe_delete import session_summary

    for report in reports:
        if report.refused is not None:
            print(
                f"[ai-hats] WARN: marker {report.marker} not swept: "
                f"{report.refused} — inspect and remove manually",
                file=sys.stderr,
            )
            continue
        if report.swept:
            print(
                f"[ai-hats] swept {len(report.swept)} entr"
                f"{'y' if len(report.swept) == 1 else 'ies'} of dead "
                f"mechanism '{report.owner_key}' ({report.marker}): "
                f"{', '.join(report.swept)} — recoverable: {session_summary()}"
            )
        for name in report.kept:
            print(
                f"[ai-hats] WARN: {report.marker.parent / name} listed by dead "
                f"mechanism '{report.owner_key}' but modified since "
                f"materialization — left in place",
                file=sys.stderr,
            )
