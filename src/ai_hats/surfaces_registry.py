"""Single source of truth for known provider surfaces (built-in + plugins).

Decoupled leaf module so `self_heal`, `providers`, and `cli` can import the
canonical surface registry without importing heavy composition layers (HATS-1095).
"""

from __future__ import annotations

from typing import NamedTuple

from .constants import PROVIDER_CLAUDE


class SurfaceInfo(NamedTuple):
    ep_name: str  # entry-point provider name, e.g. "claude", "agy", "cline"
    package_name: str | None  # PyPI package name (e.g. "ai-hats-agy"), or None if built-in
    is_builtin: bool = False  # True for in-tree built-in providers


# Canonical registry of known surfaces (state as is).
KNOWN_SURFACES: dict[str, SurfaceInfo] = {
    PROVIDER_CLAUDE: SurfaceInfo(
        ep_name=PROVIDER_CLAUDE,
        package_name=None,
        is_builtin=True,
    ),
    "agy": SurfaceInfo(
        ep_name="agy",
        package_name="ai-hats-agy",
        is_builtin=False,
    ),
    "cline": SurfaceInfo(
        ep_name="cline",
        package_name="ai-hats-cline",
        is_builtin=False,
    ),
}


def get_surface_info(provider_name: str) -> SurfaceInfo | None:
    """Get SurfaceInfo for a provider name if known."""
    return KNOWN_SURFACES.get(provider_name)
