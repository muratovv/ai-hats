"""The sweep that keeps a test run's claude traces out of the person's home."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests._claude_traces import (
    LOCK_STALE_S,
    ClaudeHome,
    Swept,
    claude_config_lock,
    runs_root,
    sweep_orphans,
    sweep_run,
)


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


def test_orphans_of_vanished_runs_go_and_live_runs_stay(home: ClaudeHome, tmp_path: Path) -> None:
    root = tmp_path / "pytest-of-u"
    (root / "pytest-8").mkdir(parents=True)
    gone = [_trace(home, root / "pytest-7/test_a0/project"), _trace(home, root / "pytest-7")]
    stay = [_trace(home, root / "pytest-8/test_a0"), _trace(home, root / "pytest-78x"), _trace(home, "/srv/work/proj")]
    _config(home, str(root / "pytest-7/test_a0/project"), str(root / "pytest-8/test_a0"), "/srv/work/proj")

    swept = sweep_orphans(home, root)

    assert (swept.deleted, swept.keys) == (2, 1)
    assert all(not d.exists() for d in gone)
    assert all(d.exists() for d in stay)
    assert sorted(json.loads(home.config.read_text())["projects"]) == sorted(
        [str(root / "pytest-8/test_a0"), "/srv/work/proj"]
    )
    assert sweep_orphans(home, root) == Swept()


def test_claude_home_follows_the_config_dir_override(tmp_path: Path) -> None:
    assert ClaudeHome.of_environ({"HOME": str(tmp_path)}) == ClaudeHome(
        projects=tmp_path / ".claude/projects", config=tmp_path / ".claude.json"
    )
    assert ClaudeHome.of_environ({"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / "c")}) == (
        ClaudeHome(projects=tmp_path / "c/projects", config=tmp_path / "c/.claude.json")
    )


def test_the_runs_root_is_where_pytest_numbers_its_runs(tmp_path: Path) -> None:
    root = runs_root({"PYTEST_DEBUG_TEMPROOT": str(tmp_path)})

    assert root.parent == tmp_path.resolve()
    assert root.name.startswith("pytest-of-")


_INNER_TEST = """
import json, os
from pathlib import Path

def test_claude_ran_here(tmp_path):
    home = Path(os.environ["CLAUDE_CONFIG_DIR"])
    key = "".join(c if c.isalnum() else "-" for c in str(tmp_path))
    (home / "projects" / key).mkdir()
    config = home / ".claude.json"
    data = json.loads(config.read_text())
    data["projects"][str(tmp_path)] = {}
    config.write_text(json.dumps(data))
"""


def test_a_session_sweeps_at_start_and_moves_its_own_at_the_end(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake))
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path / "temproot"))
    home = ClaudeHome.of_environ(os.environ)
    home.projects.mkdir(parents=True)
    orphan = _trace(home, runs_root(os.environ) / "pytest-999/test_x0")
    _config(home, "/srv/work/proj")
    pytester.makepyfile(_INNER_TEST)

    result = pytester.runpytest("-p", "tests._claude_traces")

    result.assert_outcomes(passed=1)
    basetemp = pytester.path.parent / "basetemp"
    assert not orphan.exists()
    assert list(home.projects.iterdir()) == []
    assert [p.name.startswith(_key(basetemp)) for p in (basetemp / "claude-traces").iterdir()] == [True]
    assert list(json.loads(home.config.read_text())["projects"]) == ["/srv/work/proj"]
    result.stdout.fnmatch_lines(["*deleted 1 transcript dirs*", "*moved 1 transcript dirs*dropped 1 config keys*"])


def test_a_green_run_takes_its_traces_down_with_its_basetemp(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path / "temproot"))
    (tmp_path / "temproot").mkdir()
    home = ClaudeHome.of_environ(os.environ)
    home.projects.mkdir(parents=True)
    _config(home)
    pytester.makepyfile(_INNER_TEST)

    result = pytester.runpytest_inprocess("-p", "tests._claude_traces", "-o", "tmp_path_retention_policy=failed")

    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*moved 1 transcript dirs*"])
    assert list(home.projects.iterdir()) == []
    assert list(runs_root(os.environ).iterdir()) == []


def test_the_lock_is_released_when_the_holder_fails(home: ClaudeHome) -> None:
    with pytest.raises(ValueError), claude_config_lock(home.config):
        raise ValueError

    assert not home.config.with_name(".claude.json.lock").exists()
