"""The three unnamed slots (ADR-0034 D4): ``library.default``, machine
``active``, and the paths a user config adds. Read only by the candidate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..diagnostics import Diagnostic, Level
from ..paths._dirs import user_home
from .errors import ProcessingError

_LIBRARY_KEYS = frozenset({"paths", "default"})
_LOCAL_KEYS = frozenset({"active", "venv"})


@dataclass(frozen=True)
class Slots:
    default: str | None
    active: str | None
    #: Library roots from ``~/.ai-hats/config.yaml``, not yet ``experimental/``.
    user_paths: tuple[Path, ...]
    #: Library roots from the project's ``library.paths``, same shape.
    project_paths: tuple[Path, ...]


def read_slots(project_dir: Path, config: object, diagnostics: list[Diagnostic]) -> Slots:
    """``config`` is a loaded project config. Its ``library`` block stays in
    ``_extra``, so the reference loader never applies it to ``default_role``."""
    extra = getattr(config, "_extra", {})
    default, project_paths = _library_block(extra.get("library"), project_dir)
    ai_hats_dir = getattr(config, "ai_hats_dir", ".agent/ai-hats")
    return Slots(
        default=default,
        active=_active(project_dir / ai_hats_dir / "local.yaml"),
        user_paths=_user_paths(diagnostics),
        project_paths=project_paths,
    )


def _library_block(raw: object, project_dir: Path) -> tuple[str | None, tuple[Path, ...]]:
    if raw is None:
        return None, ()
    if not isinstance(raw, Mapping):
        raise ProcessingError("library: must be a mapping")
    unknown = set(raw) - _LIBRARY_KEYS
    if unknown:
        raise ProcessingError(f"library: unknown key {sorted(unknown)[0]!r}")
    default = raw.get("default")
    if default is not None and not isinstance(default, str):
        raise ProcessingError("library.default must be an expression")
    paths = raw.get("paths") or []
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ProcessingError("library.paths must be a list of paths")
    return default or None, tuple(_resolve(project_dir, item) for item in paths)


def _active(path: Path) -> str | None:
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, Mapping):
        raise ProcessingError(f"{path}: not a mapping")
    unknown = set(raw) - _LOCAL_KEYS
    if unknown:
        raise ProcessingError(f"{path}: unknown key {sorted(unknown)[0]!r}")
    active = raw.get("active")
    if active is None:
        return None
    if not isinstance(active, str):
        raise ProcessingError(f"{path}: active must be an expression")
    return active


def _user_paths(diagnostics: list[Diagnostic]) -> tuple[Path, ...]:
    path = user_home() / ".ai-hats" / "config.yaml"
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, Mapping):
        raise ProcessingError(f"{path}: not a mapping")
    for key in sorted(set(raw) - {"library"}):
        diagnostics.append(
            Diagnostic(Level.WARN, f"unknown key {key!r}", where=path),
        )
    library = raw.get("library") or {}
    if not isinstance(library, Mapping):
        raise ProcessingError(f"{path}: library: must be a mapping")
    unknown = set(library) - {"paths"}
    if unknown:
        raise ProcessingError(f"{path}: library: unknown key {sorted(unknown)[0]!r}")
    paths = library.get("paths") or []
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ProcessingError(f"{path}: library.paths must be a list of paths")
    return tuple(Path(item).expanduser() for item in paths)


def _resolve(project_dir: Path, text: str) -> Path:
    path = Path(text).expanduser()
    return path.resolve() if path.is_absolute() else (project_dir / path).resolve()
