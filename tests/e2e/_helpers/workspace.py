"""Enumerate uv-workspace members from ``packages/*`` (HATS-895)."""

from __future__ import annotations

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
