"""The sweep that keeps a test run's claude traces out of the person's home."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests._claude_traces import LOCK_STALE_S, ClaudeHome, claude_config_lock, sweep_run


def _key(path: Path | str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(path))


@pytest.fixture
def home(tmp_path: Path) -> ClaudeHome:
    claude = ClaudeHome(projects=tmp_path / "home/.claude/projects", config=tmp_path / "home/.claude.json")
    claude.projects.mkdir(parents=True)
    return claude


def _trace(home: ClaudeHome, cwd: Path | str) -> Path:
    d = home.projects / _key(cwd)
    d.mkdir()
    (d / "s.jsonl").write_text("{}\n")
    return d


def test_run_traces_move_into_the_run_and_others_stay(home: ClaudeHome, tmp_path: Path) -> None:
    basetemp = tmp_path / "pytest-of-u/pytest-7"
    ours = [_trace(home, basetemp / "test_a0/project"), _trace(home, basetemp / "popen-gw1/wt-sandbox0/x")]
    theirs = [_trace(home, tmp_path / "pytest-of-u/pytest-70/test_b0"), _trace(home, "/srv/work/proj")]
    into = basetemp / "claude-traces"

    swept = sweep_run(home, basetemp, into)

    assert swept.moved == 2
    assert sorted(p.name for p in into.iterdir()) == sorted(d.name for d in ours)
    assert all(not d.exists() for d in ours)
    assert all(d.exists() for d in theirs)


def _config(home: ClaudeHome, *keys: str, **top: object) -> None:
    home.config.write_text(json.dumps({**top, "projects": {k: {"lastSessionId": "x"} for k in keys}}))


def test_run_config_keys_are_dropped_and_the_rest_kept(home: ClaudeHome, tmp_path: Path) -> None:
    basetemp = tmp_path / "pytest-of-u/pytest-7"
    kept = [str(tmp_path / "pytest-of-u/pytest-70/test_b0"), "/srv/work/proj"]
    _config(home, str(basetemp / "test_a0/project"), str(basetemp), *kept, oauthAccount={"id": 1})

    swept = sweep_run(home, basetemp, basetemp / "claude-traces")

    data = json.loads(home.config.read_text())
    assert swept.keys == 2
    assert sorted(data["projects"]) == sorted(kept)
    assert data["oauthAccount"] == {"id": 1}


def test_a_config_with_nothing_to_drop_is_not_rewritten(home: ClaudeHome, tmp_path: Path) -> None:
    _config(home, "/srv/work/proj")
    before = home.config.read_text()

    swept = sweep_run(home, tmp_path / "pytest-of-u/pytest-7", tmp_path / "into")

    assert swept.keys == 0
    assert home.config.read_text() == before
    assert not (tmp_path / "into").exists()


def test_a_held_claude_lock_is_waited_for_then_refused(home: ClaudeHome) -> None:
    lock = home.config.with_name(".claude.json.lock")
    lock.mkdir()

    with pytest.raises(TimeoutError, match="held longer"):
        with claude_config_lock(home.config, wait_s=0.2):
            pass

    assert lock.is_dir()


def test_a_stale_claude_lock_is_taken_over_and_released(home: ClaudeHome) -> None:
    lock = home.config.with_name(".claude.json.lock")
    lock.mkdir()
    os.utime(lock, (time.time() - LOCK_STALE_S - 5,) * 2)

    with claude_config_lock(home.config, wait_s=0.2):
        assert lock.is_dir()

    assert not lock.exists()


def test_the_lock_is_released_when_the_holder_fails(home: ClaudeHome) -> None:
    with pytest.raises(ValueError), claude_config_lock(home.config):
        raise ValueError

    assert not home.config.with_name(".claude.json.lock").exists()
