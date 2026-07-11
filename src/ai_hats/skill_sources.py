"""Open registry of skill sources — the ``ai_hats.skills`` IoC seam (HATS-871).

Mirrors the T10 provider registry: a package advertises an anchor package (whose
dir holds a ``skills/`` subdir) under the entry-point group; ai-hats discovers it
via ``importlib.metadata`` and appends the root to the resolver chain. The anchor
resolves through ``importlib.resources`` + ``as_file`` (zipimport-safe, mirrors
``paths/library.py``). See ADR-0014 §"Engine-owned skills" and ADR-0016 (the
seam is retained for out-of-tree skill sources; engine-owned skills bind to their
engine via ``requires``, not physical co-location).
"""

from __future__ import annotations

import atexit
import importlib.metadata
import logging
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path

logger = logging.getLogger(__name__)

# name -> resolved source root (the dir that CONTAINS a ``skills/`` subdir).
_SKILL_SOURCE_REGISTRY: dict[str, Path] = {}

# HATS-871 / T11: the IoC seam. A package advertises a skills root under this
# group; ai-hats discovers + registers it without importing the package directly.
SKILL_SOURCE_ENTRY_POINT_GROUP = "ai_hats.skills"

# Zipimport-safe materialisation held open for the process (mirrors library.py).
_SRC_EXITSTACK = ExitStack()
atexit.register(_SRC_EXITSTACK.close)


class SkillSourceRegistryError(RuntimeError):
    """Raised when a skill-source name is already registered."""


def register_skill_source(name: str, root: Path) -> None:
    """Register a skill-source ``root`` under ``name`` (dup-guarded)."""
    if name in _SKILL_SOURCE_REGISTRY:
        raise SkillSourceRegistryError(f"skill source already registered: {name!r}")
    _SKILL_SOURCE_REGISTRY[name] = root


def skill_source_names() -> list[str]:
    """Registered skill-source names in registration order (deterministic)."""
    return list(_SKILL_SOURCE_REGISTRY)


def skill_source_roots() -> list[Path]:
    """Resolved skill-source roots, registration order (append to library_paths)."""
    return list(_SKILL_SOURCE_REGISTRY.values())


def _reset_for_tests() -> None:
    """Clear the registry. Tests snapshot/restore around this."""
    _SKILL_SOURCE_REGISTRY.clear()


def _skill_source_entry_points():
    """Entry points advertised under the skill-source group (isolated for tests)."""
    return importlib.metadata.entry_points(group=SKILL_SOURCE_ENTRY_POINT_GROUP)


def _resolve_anchor(anchor: str) -> Path | None:
    """Resolve an anchor package name to its on-disk root, or ``None``.

    Rejects (warn+skip) an anchor that is unimportable, non-materialisable, or
    whose root has no ``skills/`` subdir (nothing to contribute).
    """
    try:
        res = files(anchor)
    except (ModuleNotFoundError, FileNotFoundError, TypeError) as exc:
        logger.warning("skill source anchor %r unresolvable: %s", anchor, exc)
        return None
    try:
        root = _SRC_EXITSTACK.enter_context(as_file(res))
    except (FileNotFoundError, OSError) as exc:
        logger.warning("skill source anchor %r not materialisable: %s", anchor, exc)
        return None
    if not (root / "skills").is_dir():
        logger.warning("skill source anchor %r has no skills/ dir; skipping", anchor)
        return None
    return root


def _load_skill_source_entry_points() -> None:
    """Discover + register skill sources via entry points (IoC).

    A duplicate name is skipped; a broken or unresolvable entry point is warned
    and skipped — discovery never breaks composition.
    """
    try:
        entry_points = list(_skill_source_entry_points())
    except Exception as exc:  # noqa: BLE001 - discovery must never break import
        logger.warning("skill source entry-point discovery failed: %s", exc)
        return
    for ep in entry_points:
        if ep.name in _SKILL_SOURCE_REGISTRY:
            continue
        try:
            root = _resolve_anchor(ep.value)
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            logger.warning("skipping skill source entry point %r: %s", ep.name, exc)
            continue
        if root is not None:
            register_skill_source(ep.name, root)


_load_skill_source_entry_points()
