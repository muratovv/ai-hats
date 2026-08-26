"""The surface registry — which surfaces exist and how one is looked up.

The contract they answer lives in the area (``ai_hats.surfaces``); this module is
the application half: the entry-point group, the alias table and the errors a
caller sees when a name does not resolve (ADR-0026 D14, HATS-1826).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .provider_entry_points import (
    _is_first_party_entry_point,
    _provider_entry_points,
)
from .surfaces import Surface

logger = logging.getLogger(__name__)

_PROVIDER_REGISTRY: dict[str, type[Surface]] = {}


class ProviderRegistryError(RuntimeError):
    """Raised when a provider name is already registered."""


def register_surface(name: str, cls: type[Surface]) -> None:
    """Register a provider class under ``name`` (dup-guarded)."""
    if name in _PROVIDER_REGISTRY:
        raise ProviderRegistryError(f"provider already registered: {name!r}")
    _PROVIDER_REGISTRY[name] = cls


def _load_provider_entry_points() -> None:
    """Register every advertised surface — ai-hats' own included (IoC).

    There is no built-in shortcut: `claude` reaches this process the same way any
    surface does, through the group ai-hats declares in its own pyproject. It used
    to self-register here first, which meant its declaration was never exercised
    and a broken one would have gone unnoticed (HATS-1826).

    A broken or duplicate third-party entry point is warned and skipped; a
    first-party one fails loudly (HATS-1121).
    """
    try:
        entry_points = list(_provider_entry_points())
    except Exception as exc:  # noqa: BLE001 - discovery must never break import
        logger.warning("provider entry-point discovery failed: %s", exc)
        return
    for ep in entry_points:
        if ep.name in _PROVIDER_REGISTRY:
            continue
        try:
            cls = ep.load()
            register_surface(ep.name, cls)
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            if isinstance(exc, AttributeError):
                logger.warning("skipping retired provider entry point %r: %s", ep.name, exc)
                continue
            if _is_first_party_entry_point(ep):
                raise
            logger.warning("skipping provider entry point %r: %s", ep.name, exc)


_ENTRY_POINTS_LOCK = threading.Lock()
_ENTRY_POINTS_LOADED = False


def _ensure_entry_points_loaded() -> None:
    global _ENTRY_POINTS_LOADED
    with _ENTRY_POINTS_LOCK:
        if not _ENTRY_POINTS_LOADED:
            _ENTRY_POINTS_LOADED = True
            _load_provider_entry_points()


PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "agy",
}


def surface_names() -> list[str]:
    """Registered provider names in registration order (deterministic)."""
    _ensure_entry_points_loaded()
    return list(_PROVIDER_REGISTRY)


class UnknownSurfaceError(ValueError):
    """Unknown provider name at ``get_surface``. Subclasses ``ValueError`` so
    existing ``except ValueError`` catchers keep working; carries ``name`` +
    ``available`` for the friendly CLI launch handler (mirrors
    ``RoleNotFoundError`` — HATS-965)."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"Unknown provider: {name}. Available: {available}")


def get_surface(name: str) -> Surface:
    """Get a provider instance for a registered surface name.

    Lookup only: an unregistered name refuses. ai-hats used to try to
    ``uv pip install`` the surface first — that bypass is closed (HATS-1826).
    """
    _ensure_entry_points_loaded()
    canonical_name = PROVIDER_ALIASES.get(name, name)
    cls = _PROVIDER_REGISTRY.get(canonical_name)
    if cls is None:
        raise UnknownSurfaceError(name, surface_names())
    return cls()


def is_surface_installed(name: str) -> bool:
    """Whether ``name`` resolves to a surface in this venv."""
    try:
        get_surface(name)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure to resolve means "not installed"
        logger.debug("surface %r does not resolve: %s", name, exc)
        return False


def detect_surface_presence(name: str, home: Path | None = None) -> bool:
    """Whether this surface's own home directory exists under ``home``.

    Where to look is the surface's answer (``detected_home_dirs``), not a table
    beside it: until HATS-1826 a hand-kept catalog carried a second copy of those
    directory names for surfaces that might not be installed — and every declared
    surface now ships with ai-hats, so a name that does not resolve is not a
    surface whose directories anyone could name.
    """
    try:
        dirs = get_surface(name).detected_home_dirs()
    except Exception as exc:  # noqa: BLE001 - an unresolvable surface has nothing to detect
        logger.debug("surface %r does not resolve, nothing to detect: %s", name, exc)
        return False
    root = home if home is not None else Path.home()
    return any((root / d).is_dir() for d in dirs)


def _reset_for_tests() -> None:
    """Clear the registry. Tests snapshot/restore around this."""
    _PROVIDER_REGISTRY.clear()
    global _ENTRY_POINTS_LOADED
    _ENTRY_POINTS_LOADED = False
