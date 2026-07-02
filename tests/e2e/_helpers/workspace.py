"""Enumerate + build uv-workspace members from ``packages/*`` (HATS-895/898)."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


def workspace_members(repo_root: Path) -> list[tuple[str, str]]:
    """Return ``(dist_name, import_name)`` for every ``packages/*`` member.

    Source-side glob, not installed metadata: an editable install's
    ``Requires-Dist`` is frozen at install time, so the dev repo's
    ``packages/*`` tree is the only truth about what the editable link
    will try to import.
    """
    members: list[tuple[str, str]] = []
    for pyproject in sorted(repo_root.glob("packages/*/pyproject.toml")):
        with pyproject.open("rb") as f:
            dist = tomllib.load(f)["project"]["name"]
        # exactly one importable top-level package per member (loud if not)
        (import_dir,) = [
            p
            for p in (pyproject.parent / "src").iterdir()
            if p.is_dir() and (p / "__init__.py").exists()
        ]
        members.append((dist, import_dir.name))
    return members


def build_workspace_member_wheels(
    repo_root: Path, out_dir: Path, env: dict | None = None
) -> Path:
    """``uv build --wheel`` every ``packages/*`` member into ``out_dir``.

    The integrator's built ``Requires-Dist`` lists bare ``ai-hats-core`` /
    ``ai-hats-wt`` (HATS-880), so a REAL non-editable install of the ai-hats
    wheel/source resolves them from the index — which 404s until they publish
    (HATS-884). Building the member wheels into a ``--find-links`` dir lets the
    install resolve them locally in the meantime (HATS-898).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for pyproject in sorted(repo_root.glob("packages/*/pyproject.toml")):
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(pyproject.parent)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
    return out_dir
