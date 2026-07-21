"""Cross-project roots registry (HATS-1081).

`~/.ai-hats/roots.yaml` is a plain list of project paths — the persistent source
for `--projects all`. `root_id` = folder name (no alias, YAGNI); a name collision is
disambiguated at read time by the full path via `--root`. The file path is
overridable via ``RACK_ROOTS_FILE`` (tests, alternate homes).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .resolver import find_project_root


class NotAProjectError(ValueError):
    """`rack root add <path>` was given a path that is not (and has no ancestor
    that is) an ai-hats project — names the offending path."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"not an ai-hats project (no .agent/ or ai-hats.yaml): {path}")


def registry_path() -> Path:
    """`~/.ai-hats/roots.yaml`, or the ``RACK_ROOTS_FILE`` override."""
    override = os.environ.get("RACK_ROOTS_FILE")
    return Path(override) if override else Path.home() / ".ai-hats" / "roots.yaml"


def load_registered_roots() -> list[Path]:
    """The registered project roots (resolved absolute paths); [] when the file is
    absent — a missing registry is empty, never an error (read-tolerant)."""
    path = registry_path()
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Path(p) for p in (data.get("roots") or [])]


def add_registered_root(path: Path) -> Path:
    """Register a project root: expand + resolve, validate it is a project, dedup,
    persist. Returns the stored (resolved) path. Non-project → :class:`NotAProjectError`."""
    resolved = path.expanduser().resolve()
    project = find_project_root(resolved)
    if project is None:
        raise NotAProjectError(str(path))
    project = project.resolve()
    roots = load_registered_roots()
    if project not in roots:
        roots.append(project)
        _save(roots)
    return project


def remove_registered_root(path: Path) -> bool:
    """Unregister a root by path; ``True`` if it was present."""
    resolved = path.expanduser().resolve()
    roots = load_registered_roots()
    kept = [r for r in roots if r != resolved]
    if len(kept) == len(roots):
        return False
    _save(kept)
    return True


def _save(roots: list[Path]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"roots": [str(r) for r in roots]}), encoding="utf-8")
