"""Single source of truth for the surfaces ai-hats knows (HATS-1095, HATS-1178).

Every surface is a symmetric entry point under the `ai_hats.providers` group
(HATS-870) — no built-in vs plugin distinction. All five listed here ship inside
`ai-hats` itself since HATS-1826; the group stays open, so a third party can
declare its own surface, but ai-hats never installs one on the user's behalf.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .surfaces import Surface


class SurfaceInfo(NamedTuple):
    ep_name: str  # entry-point provider name, e.g. "claude", "agy", "cline", "codex"
    default_home_dirs: tuple[str, ...] = ()  # default directory names under $HOME to check presence


# Canonical registry of surfaces (state as is).
KNOWN_SURFACES: dict[str, SurfaceInfo] = {
    "claude": SurfaceInfo(ep_name="claude", default_home_dirs=(".claude",)),
    "agy": SurfaceInfo(ep_name="agy", default_home_dirs=(".agy", ".gemini")),
    "cline": SurfaceInfo(ep_name="cline", default_home_dirs=(".cline",)),
    "codex": SurfaceInfo(ep_name="codex", default_home_dirs=(".codex",)),
    "opencode": SurfaceInfo(ep_name="opencode", default_home_dirs=(".opencode",)),
}


def get_surface_info(provider_name: str) -> SurfaceInfo | None:
    """Get SurfaceInfo for a provider name if known."""
    return KNOWN_SURFACES.get(provider_name)


def get_known_surfaces() -> dict[str, SurfaceInfo]:
    """Get all registered surface metadata."""
    return dict(KNOWN_SURFACES)


def get_installed_providers() -> dict[str, Surface]:
    """Get name -> Surface mapping for all currently installed & importable surfaces in venv."""
    from .surface_registry import get_surface, surface_names

    providers: dict[str, Surface] = {}
    for name in surface_names():
        try:
            providers[name] = get_surface(name)
        except Exception:  # noqa: S110
            # silent-ok: a surface that will not import is not installed
            pass
    return providers


def is_surface_installed(provider_name: str) -> bool:
    """Return True iff provider_name is installed and resolves to a Surface instance."""
    from .surface_registry import get_surface

    try:
        get_surface(provider_name)
        return True
    except Exception:  # silent-ok: a surface that will not import is not installed
        return False


def detect_surface_presence(provider_name: str, home: Path | None = None) -> bool:
    """Surface-agnostic check whether a surface (installed or known) is present on the host."""
    if home is None:
        home = Path.home()

    from .surface_registry import get_surface

    try:
        dirs = get_surface(provider_name).detected_home_dirs()
    except Exception:  # silent-ok: known metadata is the read-only fallback
        dirs = []

    if not dirs:
        info = get_surface_info(provider_name)
        if info is None:
            return False
        dirs = list(info.default_home_dirs)

    return any((home / d).is_dir() for d in dirs)
