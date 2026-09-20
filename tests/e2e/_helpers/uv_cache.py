"""Reclaim what an e2e session left in the shared uv cache.

Each ``--reinstall`` from a per-worker clone (:mod:`_helpers.repo_src`) writes
a build under a path key nobody installs from again. ``uv cache prune`` drops
the revisions superseded under one key but is path-blind, so the last one
survives the clone's ``rm -rf``; ``uv cache clean <member>…`` drops it by name
and leaves the third-party wheels that keep the install-heavy tier offline.
Safe beside a concurrent session: uv serialises cache-modifying commands, and
a venv holds clones of cache files, never links into the cache.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

# A busy cache is a neighbour mid-install: one install fits in this; a storm
# does not, and that session's own end reclaims for both.
LOCK_WAIT_S = 10
# Backstop over the lock wait plus the walk itself (0.2 s on an 850 MB cache).
COMMAND_TIMEOUT_S = 60


def reclaims_at_session_end(exitstatus: int, environ: Mapping[str, str]) -> bool:
    """Once per session, on the controller (or the serial process), after a run
    that executed tests — not on a worker, an interrupt, a usage error or an
    empty collection."""
    if environ.get("PYTEST_XDIST_WORKER"):
        return False
    return exitstatus in (0, 1)


def reclaim(
    repo_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Run the reclaim; one report line per command. Never raises."""
    uv = which("uv")
    if uv is None:
        return ["uv not on PATH — uv cache left as is"]
    env = dict(os.environ if environ is None else environ)
    env["UV_LOCK_TIMEOUT"] = str(LOCK_WAIT_S)
    report = []
    for cmd in reclaim_commands(uv, workspace_members(repo_root)):
        label = "uv " + " ".join(cmd[1:])
        try:
            cp = run(
                cmd,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            report.append(f"{label}: {exc}")
            continue
        report.append(f"{label}: {_last_line(cp)}")
    return report


def _last_line(cp: subprocess.CompletedProcess[str]) -> str:
    lines = (cp.stderr or cp.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit {cp.returncode}"


def workspace_members(repo_root: Path) -> list[str]:
    """The root project's name followed by every ``packages/*/pyproject.toml`` name."""
    names = [_project_name(repo_root / "pyproject.toml")]
    names.extend(
        _project_name(pyproject)
        for pyproject in sorted(repo_root.glob("packages/*/pyproject.toml"))
    )
    return names


def reclaim_commands(uv: str, members: list[str]) -> list[list[str]]:
    """Prune the superseded revisions, then clean the members by name."""
    return [[uv, "cache", "prune"], [uv, "cache", "clean", *members]]


def _project_name(pyproject: Path) -> str:
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["name"]
