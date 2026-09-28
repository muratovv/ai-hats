"""A test run's claude traces — dirs and config keys named by its tmp paths — and their sweep."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ai_hats.paths import claude_transcripts_dir


@dataclass(frozen=True)
class ClaudeHome:
    """Where claude keeps what it writes per project: transcript dirs and its config file."""

    projects: Path
    config: Path


@dataclass(frozen=True)
class Swept:
    moved: int = 0
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


def _drop_config_keys(config: Path, doomed: Callable[[str], bool]) -> int:
    """Remove the ``projects`` entries ``doomed`` names; the file is rewritten only if any go."""
    if not config.exists() or not any(map(doomed, _projects(config))):
        return 0
    with claude_config_lock(config):
        data = json.loads(config.read_text(encoding="utf-8"))
        projects = data.get("projects") or {}
        gone = [key for key in projects if doomed(key)]
        for key in gone:
            del projects[key]
        tmp = config.with_name(f"{config.name}.ai-hats-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        shutil.copymode(config, tmp)
        os.replace(tmp, config)
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
