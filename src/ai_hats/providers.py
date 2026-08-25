"""The surface registry — which surfaces exist and how one is looked up.

The contract they answer lives in the area (``ai_hats.surfaces``); this module is
the application half: the entry-point group, the alias table and the errors a
caller sees when a name does not resolve (ADR-0026 D14, HATS-1826).
"""

from __future__ import annotations

import logging
import threading

from .constants import PROVIDER_CLAUDE
from .provider_entry_points import (
    _is_first_party_entry_point,
    _provider_entry_points,
)
from .surfaces import Provider

logger = logging.getLogger(__name__)

# HATS-1336: no runtime-hooks owner — retiring the mechanism was HATS-905's
# designed switch, so the sweeper now reclaims the root ai-hats:* entries.

_PROVIDER_REGISTRY: dict[str, type[Provider]] = {}


class ProviderRegistryError(RuntimeError):
    """Raised when a provider name is already registered."""


def register_provider(name: str, cls: type[Provider]) -> None:
    """Register a provider class under ``name`` (dup-guarded)."""
    if name in _PROVIDER_REGISTRY:
        raise ProviderRegistryError(f"provider already registered: {name!r}")
    _PROVIDER_REGISTRY[name] = cls


def _load_provider_entry_points() -> None:
    """Discover + register out-of-tree providers via entry points (IoC).

    A built-in already self-registered wins (silent skip); a broken or duplicate
    third-party entry point is warned and skipped. First-party entry points
    shipped by ai-hats itself must fail loudly on load failure (HATS-1121).
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
            register_provider(ep.name, cls)
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            if isinstance(exc, AttributeError):
                logger.warning("skipping retired provider entry point %r: %s", ep.name, exc)
                continue
            if _is_first_party_entry_point(ep):
                raise
            logger.warning("skipping provider entry point %r: %s", ep.name, exc)


_ENTRY_POINTS_LOCK = threading.Lock()
_ENTRY_POINTS_LOADED = False


def _ensure_entry_points_loaded(force: bool = False) -> None:
    global _ENTRY_POINTS_LOADED
    with _ENTRY_POINTS_LOCK:
        if not _ENTRY_POINTS_LOADED or force:
            _ENTRY_POINTS_LOADED = True
            _register_builtins()
            _load_provider_entry_points()


PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "agy",
}


def provider_names() -> list[str]:
    """Registered provider names in registration order (deterministic)."""
    _ensure_entry_points_loaded()
    return list(_PROVIDER_REGISTRY)


class UnknownProviderError(ValueError):
    """Unknown provider name at ``get_provider``. Subclasses ``ValueError`` so
    existing ``except ValueError`` catchers keep working; carries ``name`` +
    ``available`` for the friendly CLI launch handler (mirrors
    ``RoleNotFoundError`` — HATS-965)."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"Unknown provider: {name}. Available: {available}")


def get_provider(name: str, *, auto_install: bool = True) -> Provider:
    """Get a provider instance, optionally without mutating package state."""
    _ensure_entry_points_loaded()
    canonical_name = PROVIDER_ALIASES.get(name, name)
    cls = _PROVIDER_REGISTRY.get(canonical_name)
    if cls is None:
        from .paths import editable_install_root
        from .self_heal import SURFACES_SUBPATH, ensure_surface_plugin_installed
        from .surfaces_registry import get_surface_info

        root = editable_install_root("ai-hats")
        in_tree = root.joinpath(*SURFACES_SUBPATH, canonical_name).is_dir() if root else False
        is_known = get_surface_info(canonical_name) is not None

        if auto_install and (is_known or in_tree):
            if ensure_surface_plugin_installed(canonical_name):
                import importlib

                importlib.invalidate_caches()
                _ensure_entry_points_loaded(force=True)
                cls = _PROVIDER_REGISTRY.get(canonical_name)

    if cls is None:
        raise UnknownProviderError(name, provider_names())
    return cls()


def _register_builtins() -> None:
    from ai_hats.surfaces.claude.provider import ClaudeProvider

    for name, cls in ((PROVIDER_CLAUDE, ClaudeProvider),):
        if name in _PROVIDER_REGISTRY:
            continue
        register_provider(name, cls)


def _reset_for_tests() -> None:
    """Clear the registry. Tests snapshot/restore around this."""
    _PROVIDER_REGISTRY.clear()
    global _ENTRY_POINTS_LOADED
    _ENTRY_POINTS_LOADED = False
