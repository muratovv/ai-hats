"""Shared transcript-discovery + tool-home resolution (HATS-1087)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import env


def tool_home(name: str, env_var: str) -> Path:
    """``$env_var`` or ``~/.{name}`` — the shared home-dir pattern."""
    override = env.tool_home_override(env_var)
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
    all_found = discover_all_by_mtime(transcripts_dir, glob_pattern, session_id)
    return all_found[-1] if all_found else None


def discover_all_by_mtime(
    transcripts_dir: Path,
    glob_pattern: str,
    session_id: str,
    *,
    end_ts: float | None = None,
) -> list[Path]:
    """All files matching ``glob_pattern`` with session_start <= mtime [<= end_ts], sorted by mtime (HATS-1400)."""
    if not transcripts_dir.is_dir():
        return []
    start_ts = session_start_ts(session_id)
    if start_ts is None:
        return []
    matches: list[tuple[float, Path]] = []
    for f in transcripts_dir.glob(glob_pattern):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime >= start_ts and (end_ts is None or mtime <= end_ts):
            matches.append((mtime, f))
    matches.sort(key=lambda x: (x[0], str(x[1])))

    return [p for _, p in matches]


def resolve_transcript(
    transcripts_dir: Path,
    glob_pattern: str,
    session_id: str,
    *,
    exact_path: Path | None = None,
    end_ts: float | None = None,
) -> list[Path]:
    """The transcripts that are provably ours; mtime matches when we have no id (HATS-1400).

    HATS-1400: Extended to return list[Path] to support provider surfaces that rotate
    session logs (e.g. agy brain segments). Returns empty list when none found.
    """
    if exact_path is not None:
        return [exact_path] if exact_path.exists() else []
    return discover_all_by_mtime(transcripts_dir, glob_pattern, session_id, end_ts=end_ts)


__all__ = [
    "tool_home",
    "session_start_ts",
    "discover_recent_by_mtime",
    "discover_all_by_mtime",
    "resolve_transcript",
]

