"""A test run's claude traces — dirs and config keys named by its tmp paths — and their sweep."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ai_hats.env import ENV_CLAUDE_CONFIG_DIR, ENV_HOME
from ai_hats.paths import claude_dir, claude_transcripts_dir


@dataclass(frozen=True)
class ClaudeHome:
    """Where claude keeps what it writes per project: transcript dirs and its config file."""

    projects: Path
    config: Path

    @classmethod
    def of_environ(cls, environ: Mapping[str, str]) -> ClaudeHome:
        """claude's own rule: ``$CLAUDE_CONFIG_DIR`` holds both, else ``~/.claude/`` and ``~/.claude.json``."""
        if config_dir := environ.get(ENV_CLAUDE_CONFIG_DIR):
            return cls(
                projects=Path(config_dir) / "projects", config=Path(config_dir) / ".claude.json"
            )
        home = Path(environ.get(ENV_HOME) or Path.home())
        return cls(projects=claude_dir(home) / "projects", config=home / ".claude.json")


def runs_root(environ: Mapping[str, str]) -> Path:
    """The dir pytest numbers its runs in, derived as pytest's own ``TempPathFactory`` does."""
    from _pytest.tmpdir import get_user

    temproot = Path(environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir()).resolve()
    return temproot / f"pytest-of-{get_user() or 'unknown'}"


@dataclass(frozen=True)
class Swept:
    moved: int = 0
    deleted: int = 0
    keys: int = 0


#: proper-lockfile's staleness default, which claude uses for its config lock.
LOCK_STALE_S = 10.0


@contextmanager
def claude_config_lock(config: Path, *, wait_s: float = LOCK_STALE_S) -> Iterator[None]:
    """Hold claude's own lock on ``config``: the ``<config>.lock`` dir, taken over once stale.

    claude re-reads the file under this lock before it writes, so a writer
    holding it neither loses claude's updates nor has its own undone.
    """
    lock = config.with_name(f"{config.name}.lock")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.rmdir()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} held longer than {wait_s:.0f}s") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.rmdir()


def update_claude_config(config: Path, change: Callable[[dict[str, Any]], bool]) -> None:
    """Edit claude's config as claude does: under its lock, on a fresh read, written only when ``change`` says so.

    A config that will not parse raises — never rewritten from scratch, which would drop the login.
    """
    with claude_config_lock(config):
        data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        if not change(data):
            return
        tmp = config.with_name(f"{config.name}.ai-hats-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if config.exists():
            shutil.copymode(config, tmp)
        os.replace(tmp, config)


def _drop_config_keys(config: Path, doomed: Callable[[str], bool]) -> int:
    """Remove the ``projects`` entries ``doomed`` names; the lock is taken only if any match."""
    if not config.exists() or not any(map(doomed, _projects(config))):
        return 0
    gone: list[str] = []

    def drop(data: dict[str, Any]) -> bool:
        projects = data.get("projects") or {}
        gone[:] = [key for key in projects if doomed(key)]
        for key in gone:
            del projects[key]
        return bool(gone)

    update_claude_config(config, drop)
    return len(gone)


def _projects(config: Path) -> dict[str, object]:
    data = json.loads(config.read_text(encoding="utf-8"))
    return (data.get("projects") or {}) if isinstance(data, dict) else {}


def _spellings(path: Path) -> set[str]:
    """``path`` as claude may have keyed it: as given and resolved, with and without ``/private``."""
    forms = {str(path), os.path.realpath(path)}
    return forms | {f.removeprefix("/private") for f in forms if f.startswith("/private/")}


def _dir_keys(path: Path) -> set[str]:
    return {claude_transcripts_dir(Path(form)).name for form in _spellings(path)}


def _dir_under(name: str, keys: set[str]) -> bool:
    return any(name == key or name.startswith(f"{key}-") for key in keys)


def _path_under(key: str, roots: set[str]) -> bool:
    return any(key == root or key.startswith(f"{root}/") for root in roots)


def sweep_run(home: ClaudeHome, basetemp: Path, into: Path) -> Swept:
    """Move every transcript dir claude keyed under ``basetemp`` into ``into``; drop its config keys."""
    keys = _dir_keys(basetemp)
    doomed = [d for d in home.projects.iterdir() if _dir_under(d.name, keys)]
    if doomed:
        into.mkdir(parents=True, exist_ok=True)
    for d in doomed:
        shutil.move(d, into / d.name)
    roots = _spellings(basetemp)
    dropped = _drop_config_keys(home.config, lambda key: _path_under(key, roots))
    return Swept(moved=len(doomed), keys=dropped)


def sweep_orphans(home: ClaudeHome, runs_root: Path) -> Swept:
    """Delete the traces of every ``<runs_root>/pytest-N`` that no longer exists.

    A run whose dir is still there — a concurrent one, or a red one pytest
    keeps — is left alone; its traces go with its dir, on a later call.
    """
    dir_runs = [
        re.compile(rf"^{re.escape(key)}-pytest-(\d+)(?:-|$)") for key in _dir_keys(runs_root)
    ]
    key_runs = [
        re.compile(rf"^{re.escape(root)}/pytest-(\d+)(?:/|$)") for root in _spellings(runs_root)
    ]

    def orphan(name: str, patterns: list[re.Pattern[str]]) -> bool:
        run = next((m.group(1) for p in patterns if (m := p.match(name))), None)
        return run is not None and not (runs_root / f"pytest-{run}").exists()

    doomed = [d for d in home.projects.iterdir() if orphan(d.name, dir_runs)]
    for d in doomed:
        shutil.rmtree(d)
    dropped = _drop_config_keys(home.config, lambda key: orphan(key, key_runs))
    return Swept(deleted=len(doomed), keys=dropped)


def _report(scope: str, sweep: Callable[[], Swept], into: Path | None = None) -> None:
    """Run ``sweep`` and say what it did; a failure is told, never raised — hygiene must not fail a run."""
    try:
        swept = sweep()
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"\n[claude-traces] {scope}: skipped, {exc!r}")
        return
    if swept == Swept():
        return
    done = (
        f"moved {swept.moved} transcript dirs into {into}"
        if into
        else f"deleted {swept.deleted} transcript dirs"
    )
    print(f"\n[claude-traces] {scope}: {done}, dropped {swept.keys} config keys")


def _controller(config: pytest.Config) -> bool:
    return not hasattr(config, "workerinput")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Clear what vanished runs left: killed ones, and ones from a checkout without this sweep."""
    if _controller(session.config):
        home, root = ClaudeHome.of_environ(os.environ), runs_root(os.environ)
        _report("vanished runs", lambda: sweep_orphans(home, root))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Carry this run's traces into its basetemp — before pytest reaps that dir on green."""
    factory = getattr(session.config, "_tmp_path_factory", None)
    basetemp = getattr(factory, "_basetemp", None)
    if basetemp is None or not _controller(session.config):
        return
    home, into = ClaudeHome.of_environ(os.environ), basetemp / "claude-traces"
    _report("this run", lambda: sweep_run(home, basetemp, into), into)
