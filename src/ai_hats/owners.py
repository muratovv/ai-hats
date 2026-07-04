"""Open registry of living owner mechanisms (HATS-905).

Every mechanism that materializes files outside ``<ai_hats_dir>`` registers
its ``owner_key`` at import time. The generic sweeper treats an on-disk
marker whose ``owner_key`` is absent here as belonging to a dead mechanism.
"""

from __future__ import annotations

_REGISTRY: dict[str, str] = {}


class OwnerRegistryError(KeyError):
    """Raised when an owner_key is already registered."""


def register_owner(key: str, *, module: str) -> None:
    if key in _REGISTRY:
        raise OwnerRegistryError(
            f"owner already registered: {key!r} (by {_REGISTRY[key]})"
        )
    _REGISTRY[key] = module


def is_living(key: str) -> bool:
    return key in _REGISTRY


def living_owners() -> frozenset[str]:
    return frozenset(_REGISTRY)


def registered() -> dict[str, str]:
    """owner_key -> registering module; a copy for enumeration/coverage tests."""
    return dict(_REGISTRY)


def _reset_for_tests() -> None:
    _REGISTRY.clear()
