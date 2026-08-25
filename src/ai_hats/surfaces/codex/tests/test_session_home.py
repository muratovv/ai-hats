from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_hats.surfaces.codex.session_home import normalize_rollout_paths


def _database(path: Path, rows: list[tuple[str, str]]) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
        connection.executemany("INSERT INTO threads VALUES (?, ?)", rows)
    return path


def test_normalize_rollout_paths_rewrites_only_the_exact_session_prefix(
    tmp_path: Path,
) -> None:
    base_home = tmp_path / "base"
    session_home = base_home / ".ai-hats" / "session-homes" / "project-key" / "sid"
    relative_rollout = Path("2026/08/24/rollout-thread.jsonl")
    canonical_rollout = base_home / "sessions" / relative_rollout
    canonical_rollout.parent.mkdir(parents=True)
    canonical_rollout.write_text("thread")
    old_rollout = session_home / "sessions" / relative_rollout
    unrelated = base_home / "sessions-other" / relative_rollout
    database = _database(
        tmp_path / "state_5.sqlite",
        [("owned", str(old_rollout)), ("unrelated", str(unrelated))],
    )

    result = normalize_rollout_paths(database, session_home, base_home)

    assert result.updated == 1
    assert result.remaining == 0
    assert result.removable is True
    with sqlite3.connect(database) as connection:
        rows = dict(connection.execute("SELECT id, rollout_path FROM threads"))
    assert rows == {"owned": str(canonical_rollout), "unrelated": str(unrelated)}


def test_normalize_rollout_paths_keeps_anchor_when_target_is_missing(
    tmp_path: Path,
) -> None:
    base_home = tmp_path / "base"
    session_home = base_home / ".ai-hats" / "session-homes" / "project-key" / "sid"
    old_rollout = session_home / "sessions/2026/08/24/missing.jsonl"
    database = _database(tmp_path / "state_5.sqlite", [("owned", str(old_rollout))])

    result = normalize_rollout_paths(database, session_home, base_home)

    assert result.updated == 0
    assert result.remaining == 1
    assert result.removable is False
    with sqlite3.connect(database) as connection:
        [(stored_path,)] = connection.execute("SELECT rollout_path FROM threads")
    assert stored_path == str(old_rollout)


def test_normalize_rollout_paths_rejects_an_unknown_threads_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match=r"no threads\.rollout_path column"):
        normalize_rollout_paths(database, tmp_path / "session", tmp_path / "base")
