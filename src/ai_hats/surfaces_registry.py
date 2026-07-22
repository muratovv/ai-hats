"""Single source of truth for surface plugins (HATS-1095).

Every provider surface (claude, agy, cline) is a symmetric entry point under the
`ai_hats.providers` entry-point group (HATS-870). No special built-in vs plugin
distinction — all surfaces are uniform entries in this registry.
"""

from __future__ import annotations

from typing import NamedTuple


class SurfaceInfo(NamedTuple):
    ep_name: str  # entry-point provider name, e.g. "claude", "agy", "cline"
    package_name: str  # package name: "ai-hats", "ai-hats-agy", "ai-hats-cline"


# Canonical registry of surfaces (state as is).
KNOWN_SURFACES: dict[str, SurfaceInfo] = {
    "claude": SurfaceInfo(ep_name="claude", package_name="ai-hats"),
    "agy": SurfaceInfo(ep_name="agy", package_name="ai-hats-agy"),
    "cline": SurfaceInfo(ep_name="cline", package_name="ai-hats-cline"),
}


def get_surface_info(provider_name: str) -> SurfaceInfo | None:
    """Get SurfaceInfo for a provider name if known."""
    return KNOWN_SURFACES.get(provider_name)
