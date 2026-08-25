"""Crash-safe lifecycle for Codex session homes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


SESSION_HOME_MANIFEST = ".ai-hats-session.json"


@dataclass(frozen=True)
class SessionHomeMetadata:
    """Persistent coordinates needed to reconcile a crashed session home."""

    base_home: Path
    sqlite_home: Path
    project_key: str
    session_id: str


def render_session_home_metadata(metadata: SessionHomeMetadata) -> str:
    return (
        json.dumps(
            {
                "base_home": str(metadata.base_home),
                "project_key": metadata.project_key,
                "session_id": metadata.session_id,
                "sqlite_home": str(metadata.sqlite_home),
                "version": 1,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def read_session_home_metadata(session_home: Path) -> SessionHomeMetadata:
    manifest = session_home / SESSION_HOME_MANIFEST
    try:
        payload = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Codex session-home manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError("Codex session-home manifest has an unsupported version")

    values = {name: payload.get(name) for name in ("base_home", "sqlite_home")}
    if not all(isinstance(value, str) and Path(value).is_absolute() for value in values.values()):
        raise RuntimeError("Codex session-home manifest has invalid paths")
    project_key = payload.get("project_key")
    session_id = payload.get("session_id")
    if not isinstance(project_key, str) or not project_key:
        raise RuntimeError("Codex session-home manifest has an invalid project key")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("Codex session-home manifest has an invalid session id")
    return SessionHomeMetadata(
        base_home=Path(values["base_home"]),
        sqlite_home=Path(values["sqlite_home"]),
        project_key=project_key,
        session_id=session_id,
    )


@dataclass(frozen=True)
class RolloutNormalization:
    """Result of canonicalizing one session home's rollout references."""

    updated: int
    remaining: int

    @property
    def removable(self) -> bool:
        return self.remaining == 0


def _relative_rollout(path: object, sessions_root: Path) -> Path | None:
    if not isinstance(path, str):
        return None
    try:
        relative = Path(path).relative_to(sessions_root)
    except ValueError:
        return None
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        return None
    return relative


def normalize_rollout_paths(
    database: Path,
    session_home: Path,
    base_home: Path,
) -> RolloutNormalization:
    """Canonicalize valid rollout paths owned by one durable session home."""
    source_sessions = session_home / "sessions"
    target_sessions = base_home / "sessions"
    updated = 0

    with sqlite3.connect(database, timeout=5) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        if "rollout_path" not in columns:
            raise RuntimeError("Codex state database has no threads.rollout_path column")

        rows = list(connection.execute("SELECT rollout_path FROM threads"))
        for (stored_path,) in rows:
            relative = _relative_rollout(stored_path, source_sessions)
            if relative is None:
                continue
            target = target_sessions / relative
            if not target.is_file():
                continue
            cursor = connection.execute(
                "UPDATE threads SET rollout_path = ? WHERE rollout_path = ?",
                (str(target), stored_path),
            )
            updated += cursor.rowcount

        remaining = sum(
            _relative_rollout(stored_path, source_sessions) is not None
            for (stored_path,) in connection.execute("SELECT rollout_path FROM threads")
        )

    return RolloutNormalization(updated=updated, remaining=remaining)


def normalize_session_rollout_paths(
    sqlite_home: Path,
    session_home: Path,
    base_home: Path,
) -> RolloutNormalization:
    """Normalize the session's references in every Codex state database."""
    if not sqlite_home.is_dir():
        raise RuntimeError("Codex SQLite home is unavailable")
    databases = sorted(path for path in sqlite_home.glob("state_*.sqlite") if path.is_file())
    results = [normalize_rollout_paths(path, session_home, base_home) for path in databases]
    return RolloutNormalization(
        updated=sum(result.updated for result in results),
        remaining=sum(result.remaining for result in results),
    )
