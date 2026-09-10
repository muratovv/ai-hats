"""Per-layer triage of an ai-hats install (HATS-595).

The layer decides the remediation: DATA is hand-authored (snapshot only),
MANAGED is rebuilt by ``self init``, RUNTIME by ``self update``.
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator

from .migration_assert import find_broken_hook_refs
from .migration_backup import latest_snapshot
from ai_hats_core.layout import ProjectLayout


__all__ = ["Layer", "Status", "LayerReport", "triage", "worst_status", "check_venv_consistency"]


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


def _data_reports(layout: ProjectLayout) -> list[LayerReport]:
    project_dir = layout.root
    rows = [
        _presence(Layer.DATA, "tracker", layout.tracker.base, "", project_dir),
        _presence(Layer.DATA, "user-rules", layout.user_rules, "", project_dir),
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


def _managed_reports(layout: ProjectLayout) -> list[LayerReport]:
    return [
        _presence(Layer.MANAGED, "library", layout.library.root, _INIT, layout.root),
        _hook_refs_report(layout.root),
    ]


def _drift_report(layout: ProjectLayout) -> LayerReport:
    """Drift vs upstream, read from the TTL cache — never probes the network.

    Absent or inconclusive cache is reported OK: an unknown drift is not a
    broken install, and `--check` must stay useful offline.
    """
    try:
        from .update_check import read_cache, upstream_update
    except ImportError:
        return LayerReport(Layer.RUNTIME, "version drift", Status.OK, "unknown (no update_check)")

    entry = upstream_update(layout)
    if entry is None:
        raw_entry = read_cache(layout.cache)
        if raw_entry is None or raw_entry.behind is None:
            return LayerReport(
                Layer.RUNTIME, "version drift", Status.OK, "unknown (no cached probe)"
            )
        if raw_entry.behind > 0:
            return LayerReport(
                Layer.RUNTIME,
                "version drift",
                Status.WARN,
                f"{raw_entry.behind} commit(s) behind upstream",
                _UPDATE,
            )
        return LayerReport(Layer.RUNTIME, "version drift", Status.OK, "up to date")
    return LayerReport(
        Layer.RUNTIME,
        "version drift",
        Status.WARN,
        f"{entry.behind} commit(s) behind upstream",
        _UPDATE,
    )


@contextmanager
def _collapsed_warnings() -> Iterator[None]:
    """Emit each distinct warning raised inside the block once (HATS-1163).

    A triage resolves ``ai_hats_dir`` once per check, so a path-resolution notice
    (e.g. the HATS-897 leaked-pin warning) fires once per row and buries the table
    it is printed above. Collapsing by message keeps the signal and drops the spam;
    nothing is swallowed, because every distinct message is re-raised.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    seen: set[tuple[type, str]] = set()
    for w in caught:
        key = (w.category, str(w.message))
        if key in seen:
            continue
        seen.add(key)
        warnings.warn(w.message, stacklevel=2)


def triage(layout: ProjectLayout) -> list[LayerReport]:
    """Run every layer check against the project. Read-only."""
    with _collapsed_warnings():
        reports = [
            *_data_reports(layout),
            *_managed_reports(layout),
            _drift_report(layout),
        ]
    return reports


def worst_status(reports: list[LayerReport]) -> Status:
    """The most severe status across ``reports`` (OK when empty)."""
    for severity in (Status.BROKEN, Status.WARN):
        if any(r.status is severity for r in reports):
            return severity
    return Status.OK


def check_venv_consistency(project_dir: Path) -> list[str]:
    """HATS-1234: Graded escalation ladder for venv and environment consistency.

    Hierarchy:
    - Level 1 (Local regenerable build artifacts / pycache): Auto-heal stale bytecode
      by unlinking .pyc files via _check_pycache_coherence(). If un-unlinkable stale
      .pyc files persist, report Level 1 remediation.
    - Level 2 (Editable dev env drift): Check for `stale_dev_env_warnings()`.
      Remediation: `uv sync --inexact --all-packages`.
    - Level 3 (Genuinely broken venv / missing deps): Check `find_integrity_failures()`.
      Remediation: escalate to `ai-hats self update`.

    Returns list of warning messages emitted.
    """
    from ._bootstrap import _check_pycache_coherence, find_integrity_failures
    from .env_drift import stale_dev_env_warnings

    warnings: list[str] = []

    # Level 1: Regenerable build artifacts / pycache (auto-healed inside _check_pycache_coherence)
    pycache_failures = _check_pycache_coherence()
    if pycache_failures:
        msg = (
            "[Warning] ⚠️  Level 1 (Local cache stale): un-cleared __pycache__ bytecode files remain.\n"
            "  Remediation: clear local caches (e.g. `find . -name '*.pyc' -delete` or re-activate venv)."
        )
        warnings.append(msg)

    # Level 2: Editable dev env drift
    dev_drift = stale_dev_env_warnings(repo_root=project_dir)
    if dev_drift:
        for d in dev_drift:
            msg = (
                f"[Warning] ⚠️  Level 2 (Dev env drift): {d}\n"
                "  Remediation: run `uv sync --inexact --all-packages` to sync dev dependencies."
            )
            warnings.append(msg)

    # Level 3: Genuinely broken venv (integrity failures persist after Level 1)
    integrity_failures = find_integrity_failures()
    if integrity_failures:
        detail = "\n".join(f"    - {f}" for f in integrity_failures)
        msg = (
            "[Warning] ⚠️  Level 3 (Broken venv): integrity failures detected in installed packages:\n"
            f"{detail}\n"
            "  Remediation: run `ai-hats self update` to repair environment."
        )
        warnings.append(msg)

    return warnings
