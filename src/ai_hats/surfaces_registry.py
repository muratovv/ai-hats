"""Single source of truth for surface plugins (HATS-1095, HATS-1178).

Every provider surface (claude, agy, cline) is a symmetric entry point under the
`ai_hats.providers` entry-point group (HATS-870). No special built-in vs plugin
distinction — all surfaces are uniform entries in this registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .providers import Provider


class SurfaceInfo(NamedTuple):
    ep_name: str  # entry-point provider name, e.g. "claude", "agy", "cline", "codex"
    package_name: str  # package name, e.g. "ai-hats", "ai-hats-agy", "ai-hats-codex"
    default_home_dirs: tuple[str, ...] = ()  # default directory names under $HOME to check presence


# Canonical registry of surfaces (state as is).
KNOWN_SURFACES: dict[str, SurfaceInfo] = {
    "claude": SurfaceInfo(ep_name="claude", package_name="ai-hats", default_home_dirs=(".claude",)),
    "agy": SurfaceInfo(
        ep_name="agy", package_name="ai-hats-agy", default_home_dirs=(".agy", ".gemini")
    ),
    "cline": SurfaceInfo(
        ep_name="cline", package_name="ai-hats-cline", default_home_dirs=(".cline",)
    ),
    "codex": SurfaceInfo(
        ep_name="codex", package_name="ai-hats-codex", default_home_dirs=(".codex",)
    ),
}


def get_surface_info(provider_name: str) -> SurfaceInfo | None:
    """Get SurfaceInfo for a provider name if known."""
    return KNOWN_SURFACES.get(provider_name)


def get_known_surfaces() -> dict[str, SurfaceInfo]:
    """Get all registered surface metadata."""
    return dict(KNOWN_SURFACES)


def get_installed_providers() -> dict[str, Provider]:
    """Get name -> Provider mapping for all currently installed & importable surfaces in venv."""
    from .providers import get_provider, provider_names

    providers: dict[str, Provider] = {}
    for name in provider_names():
        try:
            providers[name] = get_provider(name)
        except Exception:  # noqa: S110
            # silent-ok: a surface that will not import is not installed
            pass
    return providers


def is_surface_installed(provider_name: str) -> bool:
    """Return True iff provider_name is installed and resolves to a Provider instance."""
    from .providers import get_provider

    try:
        get_provider(provider_name, auto_install=False)
        return True
    except Exception:  # silent-ok: a surface that will not import is not installed
        return False


def detect_surface_presence(provider_name: str, home: Path | None = None) -> bool:
    """Provider-agnostic check whether a surface (installed or known) is present on the host."""
    if home is None:
        home = Path.home()

    from .providers import get_provider

    try:
        dirs = get_provider(provider_name, auto_install=False).detected_home_dirs()
    except Exception:  # silent-ok: known metadata is the read-only fallback
        dirs = []

    if not dirs:
        info = get_surface_info(provider_name)
        if info is None:
            return False
        dirs = list(info.default_home_dirs)

    return any((home / d).is_dir() for d in dirs)
