"""Shared transcript-discovery + tool-home resolution (HATS-1087)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def tool_home(name: str, env_var: str) -> Path:
    """``$env_var`` or ``~/.{name}`` — the shared home-dir pattern."""
    override = os.environ.get(env_var)
    return Path(override) if override else Path.home() / f".{name}"


def session_start_ts(session_id: str) -> float | None:
    """ai-hats ``session_id[:15]`` → UTC epoch seconds, or None on malformed.

    Deliberately NOT delegating to ``ai_hats_observe.artifacts.session_start_dt``:
    observe resolves from PyPI on a self-update install, so the integrator must
    not import a symbol newer than observe's published version (HATS-1248).
    """
    try:
        return (
            datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (ValueError, IndexError):
        return None


def discover_recent_by_mtime(
    transcripts_dir: Path,
    glob_pattern: str,
    session_id: str,
) -> Path | None:
    """Freshest file matching ``glob_pattern`` with mtime >= session start (HATS-272)."""
    if not transcripts_dir.is_dir():
        return None
    start_ts = session_start_ts(session_id)
    if start_ts is None:
        return None
    best: tuple[float, Path] | None = None
    for f in transcripts_dir.glob(glob_pattern):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime < start_ts:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, f)
    return best[1] if best else None


def resolve_transcript(
    transcripts_dir: Path,
    glob_pattern: str,
    session_id: str,
    *,
    exact_path: Path | None = None,
) -> Path | None:
    """The transcript that is provably ours; the mtime guess only when we have no id.

    HATS-1397: ``exact_path`` used to be a hint, and a miss fell through to
    ``discover_recent_by_mtime`` — the freshest file, which retroactively is a
    stranger's. A caller holding the provider's session id has an exact answer
    or none; guessing is honest only for a caller that has no id at all.
    """
    if exact_path is not None:
        return exact_path if exact_path.exists() else None
    return discover_recent_by_mtime(transcripts_dir, glob_pattern, session_id)


__all__ = [
    "tool_home",
    "session_start_ts",
    "discover_recent_by_mtime",
    "resolve_transcript",
]
