"""Polling helpers for session-dir / artefact emergence in e2e tests.

Used by tests that drive ai-hats end-to-end and need to detect when a
new ``<ai_hats_dir>/sessions/runs/session_<id>/`` directory appears
after a subprocess exits — the canonical pattern for HITL + auto-retro
flows where the auditor subprocess detaches via ``Popen(..., start_new
_session=True)`` and the test cannot wait on its pid from outside the
parent process tree.

Discovery model:

1. Snapshot existing session_dir names BEFORE the action that may spawn
   new sessions (HITL run, reviewer subprocess).
2. After the action, poll ``runs_dir`` for entries not in the snapshot
   whose ``metrics.json`` matches a role filter.
3. Return the first matching :class:`Path` or raise :class:`TimeoutError`.

Reasoning: ``metrics.json["role"]`` is the production source of truth
for the role that drove a given session — no separate
``composition.snapshot.json`` exists on disk (verified at HATS-498
pre-freeze). ``metrics.json["composition"]`` holds the full structured
composition dict (role / traits / rules / skills / provenance) — also
used by tests via :func:`read_metrics`.

The runs-dir path follows the same convention as
:func:`ai_hats.paths.runs_dir` but is inlined here so the test
infrastructure doesn't import the production package.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


def _runs_dir(project_root: Path) -> Path:
    """Canonical runs dir for a project root (``<ai_hats_dir>/sessions/runs``).

    Mirrors :func:`ai_hats.paths.runs_dir` without importing the package
    so the e2e helpers stay self-contained.
    """
    return project_root / ".agent" / "ai-hats" / "sessions" / "runs"


@dataclass(frozen=True)
class SessionDirSnapshot:
    """Baseline of ``session_<id>/`` entries under ``runs_dir`` at a moment.

    Pass to :func:`wait_for_new_session_dir` so pre-existing sessions are
    excluded from the "did a new one appear" check.
    """
    runs_dir: Path
    existing_names: frozenset[str]


def snapshot_session_dirs(project_root: Path) -> SessionDirSnapshot:
    """Capture current ``session_<id>/`` names under ``runs_dir``.

    Returns an empty set if ``runs_dir`` doesn't yet exist (typical for
    a fresh ``tmp_venv_project`` before any ai-hats command has run).
    """
    runs = _runs_dir(project_root)
    if not runs.exists():
        return SessionDirSnapshot(runs_dir=runs, existing_names=frozenset())
    names = {
        p.name for p in runs.iterdir()
        if p.is_dir() and p.name.startswith("session_")
    }
    return SessionDirSnapshot(runs_dir=runs, existing_names=frozenset(names))


def read_metrics(session_dir: Path) -> dict:
    """Load ``session_dir/metrics.json`` and return the parsed dict.

    ``metrics["composition"]`` holds the structured composition snapshot
    (``role``, ``traits``, ``rules``, ``skills``, and a ``provenance``
    sub-dict mapping each entry to its source layer:
    ``"built-in" | "global" | "project"``).

    Raises :class:`FileNotFoundError` if the file doesn't exist —
    callers should typically reach this only AFTER
    :func:`wait_for_new_session_dir` returned (which requires metrics
    to be present).
    """
    return json.loads((session_dir / "metrics.json").read_text())


def wait_for_new_session_dir(
    snapshot: SessionDirSnapshot,
    *,
    role: str,
    exclude_dirs: frozenset[Path] = frozenset(),
    timeout: float = 60.0,
    interval: float = 0.5,
) -> Path:
    """Poll ``runs_dir`` for a new ``session_<id>/`` with matching role.

    Iterates current entries minus ``snapshot.existing_names`` and
    ``exclude_dirs``, reads ``metrics.json`` from each candidate, and
    returns the first one whose ``metrics["role"] == role``.

    ``metrics.json`` may not exist yet on a freshly-created session dir
    (the directory mkdir lands before the session's first write) —
    those candidates are skipped this round and retried on the next.

    Parameters
    ----------
    snapshot:
        Baseline captured by :func:`snapshot_session_dirs` BEFORE the
        action that may spawn the session.
    role:
        Exact-match filter on ``metrics["role"]``. e.g. ``"maintainer"``
        for an HITL session or ``"session-reviewer"`` for the auto-retro
        auditor subprocess.
    exclude_dirs:
        Concrete :class:`Path` objects of previously-discovered
        session_dirs to skip. Used to find the SECOND new session after
        the FIRST has already been resolved (e.g. Phase 4 excludes the
        Phase 3 HITL session).
    timeout, interval:
        Polling bounds in seconds.

    Returns
    -------
    Path
        Absolute Path of the matching session_dir.

    Raises
    ------
    TimeoutError
        If no matching dir appears within ``timeout``. The message
        enumerates observed candidates and why each was skipped, so
        the test failure includes enough context to debug without
        re-running.

    Deliberate long helper API contract — noqa: comment-length.
    """
    deadline = time.monotonic() + timeout
    observed: dict[str, str] = {}  # name → last-seen skip reason
    while time.monotonic() < deadline:
        if snapshot.runs_dir.exists():
            for entry in snapshot.runs_dir.iterdir():
                if not (entry.is_dir() and entry.name.startswith("session_")):
                    continue
                if entry.name in snapshot.existing_names:
                    continue
                if entry in exclude_dirs:
                    continue
                metrics_path = entry / "metrics.json"
                if not metrics_path.exists():
                    observed[entry.name] = "no metrics.json yet"
                    continue
                try:
                    metrics = json.loads(metrics_path.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    observed[entry.name] = f"metrics.json unreadable: {e}"
                    continue
                actual_role = metrics.get("role")
                if actual_role == role:
                    return entry
                observed[entry.name] = f"role={actual_role!r} != {role!r}"
        time.sleep(interval)
    obs_str = (
        "\n".join(f"  {name}: {reason}" for name, reason in observed.items())
        or "  (no new dirs appeared)"
    )
    raise TimeoutError(
        f"No new session_dir with role={role!r} appeared in "
        f"{snapshot.runs_dir} within {timeout:.1f}s.\n"
        f"Observed (excluding pre-snapshot + exclude_dirs):\n{obs_str}"
    )


def wait_for_file(
    path: Path,
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
    min_size: int = 1,
) -> bytes:
    """Poll for a file to appear with at least ``min_size`` bytes.

    Returns the file's contents (bytes) once the size threshold is met.
    The function does NOT enforce read-stability across two consecutive
    polls — if your caller writes the file atomically (rename / fsync),
    a single read past ``min_size`` is enough; if not, the caller
    should re-read after a short delay.

    Raises :class:`TimeoutError` if the file never appears or stays
    below ``min_size`` within ``timeout``. The message includes the
    last observed size for debuggability.
    """
    deadline = time.monotonic() + timeout
    last_size = -1
    while time.monotonic() < deadline:
        if path.exists():
            try:
                data = path.read_bytes()
                if len(data) >= min_size:
                    return data
                last_size = len(data)
            except OSError:
                pass
        time.sleep(interval)
    raise TimeoutError(
        f"File {path} did not reach min_size={min_size} bytes within "
        f"{timeout:.1f}s. Last observed size: {last_size}."
    )
