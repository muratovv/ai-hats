"""Per-layer triage of an ai-hats install (HATS-595).

The layer decides the remediation: DATA is hand-authored (snapshot only),
MANAGED is rebuilt by ``self init``, RUNTIME by ``self update``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .constants import USER_RULES_SUBDIR
from .migration_assert import find_broken_hook_refs
from .migration_backup import latest_snapshot
from .paths import ai_hats_dir, hooks_dir, library_dir, tracker_dir
from .update_check import read_cache

__all__ = ["Layer", "Status", "LayerReport", "triage", "worst_status"]


class Layer(str, Enum):
    DATA = "DATA"
    MANAGED = "MANAGED"
    RUNTIME = "RUNTIME"


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    BROKEN = "broken"


@dataclass(frozen=True)
class LayerReport:
    """One check's verdict.

    Attributes:
        layer: Which recovery class the checked artefact belongs to.
        name: Short artefact label, unique within a triage run.
        status: Verdict; only ``BROKEN`` drives a non-zero exit.
        detail: What was observed.
        remediation: Exact command or action to fix it; empty when OK.
    """

    layer: Layer
    name: str
    status: Status
    detail: str
    remediation: str = ""


_INIT = "ai-hats self init"
_UPDATE = "ai-hats self update"


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def _presence(
    layer: Layer,
    name: str,
    path: Path,
    remediation: str,
    project_dir: Path,
) -> LayerReport:
    shown = _rel(path, project_dir)
    if path.is_dir() or path.is_file():
        return LayerReport(layer, name, Status.OK, shown)
    return LayerReport(layer, name, Status.BROKEN, f"missing: {shown}", remediation)


def _data_remediation(project_dir: Path) -> str:
    """Recovery line for a lost DATA artefact — never a heal, always a pointer."""
    snapshot = latest_snapshot(project_dir)
    if snapshot is None:
        return "no snapshot found — DATA is hand-authored and cannot be rebuilt"
    return f"tar -xzf {snapshot} -C {project_dir}"


def _data_reports(project_dir: Path) -> list[LayerReport]:
    base = ai_hats_dir(project_dir)
    rows = [
        _presence(Layer.DATA, "tracker", tracker_dir(project_dir), "", project_dir),
        _presence(Layer.DATA, "user-rules", base / USER_RULES_SUBDIR, "", project_dir),
    ]
    # Resolve the snapshot only when something is actually broken.
    if all(r.status is Status.OK for r in rows):
        return rows
    fix = _data_remediation(project_dir)
    return [r if r.status is Status.OK else replace(r, remediation=fix) for r in rows]


def _hook_refs_report(project_dir: Path) -> LayerReport:
    broken = find_broken_hook_refs(project_dir)
    if not broken:
        return LayerReport(Layer.MANAGED, "hook refs", Status.OK, "all commands resolve")
    detail = "; ".join(f"{b.event}: {b.command}" for b in broken)
    return LayerReport(Layer.MANAGED, "hook refs", Status.BROKEN, detail, _INIT)


def _managed_reports(project_dir: Path) -> list[LayerReport]:
    base = ai_hats_dir(project_dir)
    return [
        _presence(Layer.MANAGED, "imports.md", base / "imports.md", _INIT, project_dir),
        _presence(Layer.MANAGED, "library", library_dir(project_dir), _INIT, project_dir),
        _presence(Layer.MANAGED, "library/hooks", hooks_dir(project_dir), _INIT, project_dir),
        _hook_refs_report(project_dir),
    ]


def _drift_report(project_dir: Path) -> LayerReport:
    """Drift vs upstream, read from the TTL cache — never probes the network.

    Absent or inconclusive cache is reported OK: an unknown drift is not a
    broken install, and `--check` must stay useful offline.
    """
    entry = read_cache(project_dir)
    if entry is None or entry.behind is None:
        return LayerReport(Layer.RUNTIME, "version drift", Status.OK, "unknown (no cached probe)")
    if not entry.has_update:
        return LayerReport(Layer.RUNTIME, "version drift", Status.OK, "up to date")
    return LayerReport(
        Layer.RUNTIME,
        "version drift",
        Status.WARN,
        f"{entry.behind} commit(s) behind upstream",
        _UPDATE,
    )


def triage(project_dir: Path) -> list[LayerReport]:
    """Run every layer check against ``project_dir``. Read-only."""
    return [
        *_data_reports(project_dir),
        *_managed_reports(project_dir),
        _drift_report(project_dir),
    ]


def worst_status(reports: list[LayerReport]) -> Status:
    """The most severe status across ``reports`` (OK when empty)."""
    for severity in (Status.BROKEN, Status.WARN):
        if any(r.status is severity for r in reports):
            return severity
    return Status.OK
